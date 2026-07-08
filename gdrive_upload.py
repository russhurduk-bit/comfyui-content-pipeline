"""
Helper for uploading files to Google Drive over OAuth.

Configuration (environment variables):
  GOOGLE_OAUTH_CRED   Path to the OAuth credentials JSON (required).
  GDRIVE_FOLDER_CACHE Path to a small folder-id cache file (optional).
"""
import json, os, time
from pathlib import Path

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

CRED_PATH = os.environ.get(
    "GOOGLE_OAUTH_CRED",
    str(Path.home() / ".config" / "content-pipeline" / "google_oauth_cred.json"))
FOLDER_CACHE = os.environ.get(
    "GDRIVE_FOLDER_CACHE", str(Path(__file__).resolve().parent / ".gdrive_folder.json"))


def _creds():
    raw = json.load(open(CRED_PATH))
    tk = raw['oauthTokenData']
    c = Credentials(
        token=tk.get('access_token'),
        refresh_token=tk.get('refresh_token'),
        client_id=raw['clientId'],
        client_secret=raw['clientSecret'],
        token_uri='https://oauth2.googleapis.com/token',
        scopes=tk.get('scope', '').split(),
    )
    if not c.valid:
        c.refresh(Request())
    return c


def svc():
    return build('drive', 'v3', credentials=_creds(), cache_discovery=False)


def get_or_create_folder(name, parent=None):
    """Return a folder_id. Caches it in FOLDER_CACHE for reuse."""
    if os.path.exists(FOLDER_CACHE):
        d = json.load(open(FOLDER_CACHE))
        if d.get('name') == name:
            return d['id']
    s = svc()
    q = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent:
        q += f" and '{parent}' in parents"
    res = s.files().list(q=q, fields='files(id,name)', pageSize=5).execute()
    files = res.get('files', [])
    if files:
        fid = files[0]['id']
    else:
        body = {'name': name, 'mimeType': 'application/vnd.google-apps.folder'}
        if parent:
            body['parents'] = [parent]
        f = s.files().create(body=body, fields='id').execute()
        fid = f['id']
        # Share with anyone-with-the-link (viewer).
        s.permissions().create(fileId=fid, body={'role': 'reader', 'type': 'anyone'}).execute()
    json.dump({'name': name, 'id': fid}, open(FOLDER_CACHE, 'w'))
    return fid


def upload(local_path, folder_id, remote_name=None):
    """Upload a file with size verification and 3x retry. Raises on failure."""
    remote_name = remote_name or os.path.basename(local_path)
    local_size = os.path.getsize(local_path)
    last_err = None
    for attempt in range(1, 4):
        s = svc()
        # Remove any existing file with the same name before each attempt.
        q = f"name='{remote_name}' and '{folder_id}' in parents and trashed=false"
        existing = s.files().list(q=q, fields='files(id)').execute().get('files', [])
        for e in existing:
            try:
                s.files().delete(fileId=e['id']).execute()
            except Exception:
                pass
        try:
            media = MediaFileUpload(local_path, mimetype='video/mp4', resumable=True,
                                    chunksize=8 * 1024 * 1024)
            req = s.files().create(body={'name': remote_name, 'parents': [folder_id]},
                                   media_body=media, fields='id,webViewLink,name,size')
            resp = req.execute()
            # VERIFY: Drive-reported size must match the local size.
            fid_new = resp['id']
            meta = s.files().get(fileId=fid_new, fields='size,name').execute()
            drive_size = int(meta.get('size', 0))
            if drive_size == local_size:
                return resp
            last_err = f'size mismatch attempt {attempt}: local={local_size} drive={drive_size}'
        except Exception as e:
            last_err = f'attempt {attempt}: {type(e).__name__}: {str(e)[:150]}'
        time.sleep(5 * attempt)
    raise RuntimeError(f'upload failed after 3 attempts: {last_err}')


def folder_link(folder_id):
    return f'https://drive.google.com/drive/folders/{folder_id}'


if __name__ == '__main__':
    import sys
    name = sys.argv[1] if len(sys.argv) > 1 else 'Content Pipeline Output'
    fid = get_or_create_folder(name)
    print(f'folder_id: {fid}')
    print(f'link: {folder_link(fid)}')
