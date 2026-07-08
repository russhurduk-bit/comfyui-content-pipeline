#!/usr/bin/env python3
"""
Content Pipeline — Google Drive bridge.
Wraps the bundled gdrive_upload.py.
"""

import os

from gdrive_upload import (
    upload as _gdrive_upload, get_or_create_folder, folder_link, svc, CRED_PATH,
)


def check_credentials() -> bool:
    """Check if Google Drive credentials exist."""
    return os.path.exists(CRED_PATH)


def upload_file(local_path: str, folder_id: str, remote_name: str | None = None) -> dict:
    """
    Upload a single file to Google Drive folder.
    Returns {"success": bool, "drive_file_id": str, "drive_link": str}
    """
    try:
        resp = _gdrive_upload(local_path, folder_id, remote_name)
        return {
            "success": True,
            "drive_file_id": resp.get("id", ""),
            "drive_link": resp.get("webViewLink", ""),
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
        }


def ensure_folder(name: str, parent_id: str | None = None) -> str:
    """Get or create a Drive folder. Returns folder_id."""
    return get_or_create_folder(name, parent_id)


def get_folder_link(folder_id: str) -> str:
    """Get web link for a Drive folder."""
    return folder_link(folder_id)
