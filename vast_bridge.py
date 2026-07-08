#!/usr/bin/env python3
"""
Content Pipeline — Vast.ai bridge.
Wraps the bundled backends/vast_helper.py and backends/comfy_client.py.
ComfyUI on Vast is reached over an SSH tunnel (default localhost:28188).

Configuration (environment variables):
  VAST_COMFY_URL       Tunnel URL to the Vast ComfyUI (default http://localhost:28188)
  VIDEO_WORKFLOW_PATH  Path to the i2v workflow API JSON (default workflows/video.json)
"""

import os, json, copy, uuid, asyncio
from pathlib import Path

from backends.vast_helper import (
    list_instances, show_instance, create_instance, search_offers,
    wait_running, public_endpoint, user as vast_user
)
from backends.comfy_client import ComfyClient

BASE_DIR = Path(__file__).parent
VIDEO_WORKFLOW_PATH = os.environ.get(
    "VIDEO_WORKFLOW_PATH", str(BASE_DIR / "workflows" / "video.json"))
VAST_COMFYUI_URL = os.environ.get("VAST_COMFY_URL", "http://localhost:28188")

RESULTS_DIR = BASE_DIR / "results"


def get_vast_balance() -> float | None:
    """Get current Vast.ai balance in USD."""
    try:
        u = vast_user()
        return float(u.get("credit", 0))
    except Exception:
        return None


def get_vast_instances() -> list[dict]:
    """List all Vast instances."""
    try:
        return list_instances()
    except Exception:
        return []


def get_vast_status() -> dict:
    """Combined status: balance + instances + tunnel health."""
    result = {
        "balance": get_vast_balance(),
        "instances": [],
        "tunnel_ok": False,
    }

    try:
        instances = list_instances()
        result["instances"] = [
            {
                "id": i["id"],
                "status": i.get("actual_status", "unknown"),
                "gpu": i.get("gpu_name", "?"),
                "cost_per_hr": i.get("dph_total", 0),
                "label": i.get("label", ""),
            }
            for i in instances
        ]
    except Exception:
        pass

    # Check tunnel
    try:
        import requests
        r = requests.get(f"{VAST_COMFYUI_URL}/system_stats", timeout=5)
        result["tunnel_ok"] = r.ok
    except Exception:
        pass

    return result


def stop_instance(instance_id: int) -> bool:
    """Stop (not destroy!) a Vast instance."""
    try:
        import requests
        from backends.vast_helper import _h, API
        r = requests.put(
            f"{API}/instances/{instance_id}/",
            headers=_h(),
            json={"target_status": "stopped"},
            timeout=30
        )
        return r.ok
    except Exception:
        return False


def start_instance(instance_id: int) -> bool:
    """Start a stopped Vast instance."""
    try:
        import requests
        from backends.vast_helper import _h, API
        r = requests.put(
            f"{API}/instances/{instance_id}/",
            headers=_h(),
            json={"target_status": "running"},
            timeout=30
        )
        return r.ok
    except Exception:
        return False


def _load_video_workflow() -> dict:
    """Load the production video workflow JSON."""
    with open(VIDEO_WORKFLOW_PATH) as f:
        return json.load(f)


def _get_comfy_client() -> ComfyClient:
    """Get ComfyClient pointed at Vast tunnel."""
    return ComfyClient(VAST_COMFYUI_URL, http_timeout=120, job_timeout=3600)


async def generate_video(photo_path: str, video_ref_path: str,
                         positive_prompt: str, negative_prompt: str,
                         seed: int | None = None,
                         on_progress=None) -> dict:
    """
    Generate one video on Vast.ai ComfyUI.
    photo_path: local path to the source photo
    video_ref_path: local path to the reference video
    positive_prompt, negative_prompt: text prompts

    Returns {"success": bool, "filename": str, "output_path": str, "prompt_id": str}
    """
    client = _get_comfy_client()

    # Upload photo with uuid prefix
    photo_basename = os.path.basename(photo_path)
    comfy_photo = f"{uuid.uuid4().hex[:8]}_{photo_basename}"
    # Rename locally for upload
    import shutil
    tmp_photo = f"/tmp/{comfy_photo}"
    shutil.copy2(photo_path, tmp_photo)
    uploaded_photo = client.upload_image(tmp_photo)

    # Upload video ref
    video_basename = os.path.basename(video_ref_path)
    comfy_video = f"{uuid.uuid4().hex[:8]}_{video_basename}"
    tmp_video = f"/tmp/{comfy_video}"
    shutil.copy2(video_ref_path, tmp_video)
    # Upload video as input
    uploaded_video = client.upload_image(tmp_video)

    # Load and patch workflow
    workflow = _load_video_workflow()
    wf = copy.deepcopy(workflow)

    # Patch nodes:
    # 76 = LoadImage (photo)
    wf["76"]["inputs"]["image"] = uploaded_photo
    # 75 = VHS_LoadVideo (video ref)
    wf["75"]["inputs"]["video"] = uploaded_video
    # 493 = CLIPTextEncode (positive)
    wf["493"]["inputs"]["text"] = positive_prompt
    # 488 = CLIPTextEncode (negative)
    if negative_prompt:
        wf["488"]["inputs"]["text"] = negative_prompt
    # 273 = WanVideoSampler (seed)
    seed_val = seed or __import__("random").randint(1, 999999999999)
    wf["273"]["inputs"]["seed"] = seed_val

    # Submit
    prompt_id = client.submit(wf)

    # Wait with progress callback
    hist = await client.wait(prompt_id, on_progress=on_progress)

    # Download outputs
    out_name = f"video_{uuid.uuid4().hex[:8]}.mp4"
    out_path = str(RESULTS_DIR / out_name)
    saved = client.collect_outputs(hist, str(RESULTS_DIR))

    if saved:
        # Rename first output to our name
        import shutil
        shutil.move(saved[0], out_path)
        return {
            "success": True,
            "filename": out_name,
            "output_path": out_path,
            "prompt_id": prompt_id,
            "seed": seed_val,
        }

    return {
        "success": False,
        "error": "No output files",
        "prompt_id": prompt_id,
    }


# Cleanup
def _cleanup_tmp():
    import glob
    for f in glob.glob("/tmp/*.png") + glob.glob("/tmp/*.jpg") + glob.glob("/tmp/*.mp4"):
        try:
            if os.path.basename(f).startswith(("gen_", "faceswap_", "clean_", "video_")):
                os.unlink(f)
        except Exception:
            pass
