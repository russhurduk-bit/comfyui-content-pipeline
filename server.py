#!/usr/bin/env python3
"""
Content Pipeline Panel — FastAPI backend.
Port 8687, systemd comfyui-batch.service.
"""

import os, json, uuid, asyncio
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

import database as db
import pipeline
import vast_bridge
import drive_bridge
from gpu_guard import ensure_gpu_free, get_gpu_stats

app = FastAPI(title="Content Pipeline")

BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
RESULTS_DIR = BASE_DIR / "results"
FACES_DIR = UPLOAD_DIR / "faces"
VIDEO_REFS_DIR = UPLOAD_DIR / "video_refs"

for d in [UPLOAD_DIR, RESULTS_DIR, FACES_DIR, VIDEO_REFS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Initialize database on startup
db.init_db()


# ==================== Settings ====================

@app.get("/api/settings")
async def get_settings():
    settings = db.get_all_settings()
    # Mask Vast API key
    if "vast_api_key" in settings:
        key = settings["vast_api_key"]
        settings["vast_api_key_masked"] = key[:4] + "****" + key[-4:] if len(key) > 8 else "****"
        settings["vast_api_key_set"] = True
    else:
        settings["vast_api_key_set"] = False
    settings.pop("vast_api_key", None)
    settings["drive_credentials_exist"] = drive_bridge.check_credentials()
    return settings


@app.put("/api/settings")
async def update_settings(data: dict):
    allowed = {"vast_api_key", "drive_default_folder", "default_positive_prompt",
               "default_negative_prompt", "default_face_ref"}
    for key, value in data.items():
        if key in allowed:
            db.set_setting(key, str(value))
            # Also write vast key to file for vast_helper.py (path from env).
            if key == "vast_api_key" and value:
                key_file = os.environ.get(
                    "VAST_API_KEY_FILE",
                    os.path.expanduser("~/.config/vastai/vast_api_key"))
                p = Path(key_file)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(value.strip())
    return {"ok": True}


# ==================== Lines CRUD ====================

@app.get("/api/lines")
async def list_lines():
    return db.list_lines()


@app.post("/api/lines")
async def create_line(data: dict):
    name = data.get("name", "").strip()
    if not name:
        raise HTTPException(400, "Name required")
    kwargs = {}
    for k in ("positive_prompt", "negative_prompt", "drive_folder_id", "face_ref_name"):
        if k in data and data[k]:
            kwargs[k] = data[k]
    line_id = db.create_line(name, **kwargs)
    return {"id": line_id, "ok": True}


@app.get("/api/lines/{line_id}")
async def get_line(line_id: int):
    line = db.get_line(line_id)
    if not line:
        raise HTTPException(404, "Line not found")
    return line


@app.put("/api/lines/{line_id}")
async def update_line(line_id: int, data: dict):
    allowed = {"name", "positive_prompt", "negative_prompt", "drive_folder_id",
               "face_ref_name", "status", "photo_ref_path", "video_ref_path"}
    updates = {k: v for k, v in data.items() if k in allowed}
    db.update_line(line_id, **updates)
    return {"ok": True}


@app.delete("/api/lines/{line_id}")
async def delete_line(line_id: int):
    db.delete_line(line_id)
    return {"ok": True}


@app.post("/api/lines/{line_id}/auto-prompt")
async def auto_prompt(line_id: int, data: dict = None):
    """Auto-generate the video positive prompt from a reference image (Gemini vision).

    Picks the best available image for the line: an accepted photo, then any photo,
    then the reference photo, then an explicitly passed image. Falls back to a strong
    default prompt if vision fails/refuses so the user is never blocked.
    """
    line = db.get_line(line_id)
    if not line:
        raise HTTPException(404, "Line not found")

    img = None
    # 1. explicit image (e.g. freshly uploaded body) — {"image": "<filename in uploads>"}
    if data and data.get("image"):
        cand = UPLOAD_DIR / os.path.basename(data["image"])
        if cand.exists():
            img = str(cand)
    # 2. an accepted photo, else any photo
    if not img:
        try:
            photos = db.get_photos_by_line(line_id, qa_status="accepted")
        except TypeError:
            photos = db.get_photos_by_line(line_id)
        if not photos:
            try:
                photos = db.get_photos_by_line(line_id)
            except Exception:
                photos = []
        for p in photos or []:
            cand = RESULTS_DIR / p["filename"]
            if cand.exists():
                img = str(cand)
                break
    # 3. the line's reference photo
    if not img:
        ref = line.get("photo_ref_path")
        if ref and os.path.exists(ref):
            img = ref

    if not img:
        raise HTTPException(400, "No photo to analyze — upload a body photo or generate variations first")

    loop = asyncio.get_event_loop()
    positive = await loop.run_in_executor(None, pipeline.describe_video_prompt, img)
    fallback = False
    if not positive:
        positive = pipeline.DEFAULT_VIDEO_POSITIVE
        fallback = True

    db.update_line(line_id, positive_prompt=positive)
    return {"positive": positive, "fallback": fallback, "source": os.path.basename(img)}


# ==================== File Uploads ====================

@app.post("/api/upload/body")
async def upload_body(file: UploadFile = File(...)):
    """Upload body image for faceswap."""
    ext = Path(file.filename).suffix or ".png"
    name = f"body_{uuid.uuid4().hex[:8]}{ext}"
    path = UPLOAD_DIR / name
    content = await file.read()
    path.write_bytes(content)
    return {"filename": name, "path": str(path), "url": f"/api/files/uploads/{name}"}


@app.post("/api/upload/video-ref/{line_id}")
async def upload_video_ref(line_id: int, file: UploadFile = File(...)):
    """Upload video reference for a line."""
    ext = Path(file.filename).suffix or ".mp4"
    name = f"vref_{line_id}_{uuid.uuid4().hex[:8]}{ext}"
    path = VIDEO_REFS_DIR / name
    content = await file.read()
    path.write_bytes(content)
    db.update_line(line_id, video_ref_path=str(path))
    return {"filename": name, "path": str(path), "url": f"/api/files/video_refs/{name}"}


@app.post("/api/upload/face")
async def upload_face(file: UploadFile = File(...)):
    """Upload a face preset image."""
    ext = Path(file.filename).suffix or ".jpg"
    name = f"{Path(file.filename).stem}{ext}"
    path = FACES_DIR / name
    content = await file.read()
    path.write_bytes(content)
    return {"filename": name, "url": f"/api/files/faces/{name}"}


@app.get("/api/face-presets")
async def list_face_presets():
    """List available face reference images."""
    faces = []
    for f in sorted(FACES_DIR.iterdir()):
        if f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
            faces.append({
                "name": f.name,
                "url": f"/api/files/faces/{f.name}",
            })
    return faces


@app.get("/api/files/{ftype}/{filename}")
async def serve_file(ftype: str, filename: str):
    """Serve uploaded/result files."""
    type_map = {
        "uploads": UPLOAD_DIR,
        "results": RESULTS_DIR,
        "faces": FACES_DIR,
        "video_refs": VIDEO_REFS_DIR,
    }
    base = type_map.get(ftype)
    if not base:
        raise HTTPException(404, "Invalid file type")
    path = base / filename
    if not path.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(path)


# ==================== GPU Status ====================

@app.get("/api/gpu/status")
async def gpu_status():
    loop = asyncio.get_event_loop()
    stats = await loop.run_in_executor(None, get_gpu_stats)
    if stats is None:
        return {"online": False, "message": "PC unavailable"}
    return {"online": True, **stats}


# ==================== Pipeline: Faceswap ====================

@app.websocket("/api/lines/{line_id}/faceswap")
async def ws_faceswap(ws: WebSocket, line_id: int):
    await ws.accept()
    try:
        config = await ws.receive_json()
        body_image = config.get("body_image")  # filename in uploads/
        face_ref = config.get("face_ref", "reference_face.jpg")

        if not body_image:
            await ws.send_json({"type": "error", "message": "No body image"})
            return

        body_path = str(UPLOAD_DIR / body_image)
        if not os.path.exists(body_path):
            await ws.send_json({"type": "error", "message": "Body image not found"})
            return

        await ws.send_json({"type": "status", "message": "Checking GPU..."})

        async with pipeline.gpu_lock:
            loop = asyncio.get_event_loop()
            gpu_ok = await loop.run_in_executor(None, ensure_gpu_free, "photo")
            if not gpu_ok:
                await ws.send_json({"type": "error", "message": "GPU unavailable"})
                return

            await ws.send_json({"type": "status", "message": "Running faceswap..."})
            db.upsert_step(line_id, "faceswap", input_path=body_path, status="running")

            result = await pipeline.run_faceswap(body_path, face_ref)

        if result["success"]:
            db.upsert_step(line_id, "faceswap",
                           output_path=result["output_path"],
                           comfy_prompt_id=result.get("prompt_id"),
                           seed=result.get("seed"),
                           status="done")
            db.update_line(line_id, status="faceswap")
            await ws.send_json({
                "type": "result",
                "filename": result["filename"],
                "url": f"/api/files/results/{result['filename']}",
            })
        else:
            db.upsert_step(line_id, "faceswap", status="failed")
            await ws.send_json({"type": "error", "message": result.get("error", "Faceswap failed")})

        await ws.send_json({"type": "done"})
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await ws.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass


# ==================== Pipeline: Clean ====================

@app.websocket("/api/lines/{line_id}/clean")
async def ws_clean(ws: WebSocket, line_id: int):
    await ws.accept()
    try:
        config = await ws.receive_json()
        custom_prompt = config.get("prompt")

        # Get faceswap result as input
        step = db.get_step(line_id, "faceswap")
        if not step or step["status"] != "done":
            await ws.send_json({"type": "error", "message": "Faceswap not done yet"})
            return

        source_path = step["output_path"]
        if not os.path.exists(source_path):
            await ws.send_json({"type": "error", "message": "Faceswap result file missing"})
            return

        await ws.send_json({"type": "status", "message": "Checking GPU..."})

        async with pipeline.gpu_lock:
            loop = asyncio.get_event_loop()
            gpu_ok = await loop.run_in_executor(None, ensure_gpu_free, "photo")
            if not gpu_ok:
                await ws.send_json({"type": "error", "message": "GPU unavailable"})
                return

            await ws.send_json({"type": "status", "message": "Cleaning image..."})
            kwargs = {}
            if custom_prompt:
                kwargs["clean_prompt"] = custom_prompt
            db.upsert_step(line_id, "clean", input_path=source_path, status="running",
                           prompt_text=custom_prompt or "default")

            result = await pipeline.run_clean(source_path, **kwargs)

        if result["success"]:
            db.upsert_step(line_id, "clean",
                           output_path=result["output_path"],
                           comfy_prompt_id=result.get("prompt_id"),
                           seed=result.get("seed"),
                           status="done")
            db.update_line(line_id, status="clean")
            await ws.send_json({
                "type": "result",
                "filename": result["filename"],
                "url": f"/api/files/results/{result['filename']}",
            })
        else:
            db.upsert_step(line_id, "clean", status="failed")
            await ws.send_json({"type": "error", "message": result.get("error", "Clean failed")})

        await ws.send_json({"type": "done"})
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await ws.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass


# ==================== Pipeline: Set Reference ====================

@app.post("/api/lines/{line_id}/set-reference")
async def set_reference(line_id: int):
    """Set clean result as the line's photo reference."""
    step = db.get_step(line_id, "clean")
    if not step or step["status"] != "done":
        raise HTTPException(400, "Clean step not done")
    db.update_line(line_id, photo_ref_path=step["output_path"], status="reference_set")
    return {"ok": True, "photo_ref_path": step["output_path"]}


# ==================== Pipeline: Accept/Reject Step ====================

@app.post("/api/lines/{line_id}/accept-step/{step_type}")
async def accept_step(line_id: int, step_type: str, data: dict):
    accepted = data.get("accepted", True)
    db.upsert_step(line_id, step_type, accepted=1 if accepted else -1)
    return {"ok": True}


# ==================== Pipeline: Generate Photos ====================

@app.websocket("/api/lines/{line_id}/generate-photos")
async def ws_generate_photos(ws: WebSocket, line_id: int):
    await ws.accept()
    try:
        config = await ws.receive_json()
        count = min(config.get("count", 10), 50)
        toggles = config.get("toggles", {})
        verify_detail = config.get("verify_detail", False)

        line = db.get_line(line_id)
        if not line:
            await ws.send_json({"type": "error", "message": "Line not found"})
            return

        ref_path = line.get("photo_ref_path")
        if not ref_path or not os.path.exists(ref_path):
            await ws.send_json({"type": "error", "message": "No reference photo set"})
            return

        await ws.send_json({"type": "status", "message": "Checking GPU..."})

        async with pipeline.gpu_lock:
            loop = asyncio.get_event_loop()
            gpu_ok = await loop.run_in_executor(None, ensure_gpu_free, "photo")
            if not gpu_ok:
                await ws.send_json({"type": "error", "message": "GPU unavailable"})
                return

            await ws.send_json({"type": "status", "message": f"Generating {count} photos..."})
            db.update_line(line_id, status="photos")

            used_descriptions = set()
            generated = 0

            for i in range(count):
                # Build unique variation
                for _ in range(20):
                    prompt_text, description = pipeline.build_prompt_text(toggles, i)
                    if description not in used_descriptions:
                        used_descriptions.add(description)
                        break

                await ws.send_json({
                    "type": "progress",
                    "current": i + 1,
                    "total": count,
                    "description": description,
                })

                result = await pipeline.run_photo_variation(
                    ref_path, toggles, i, verify_detail
                )

                if result["success"]:
                    photo_id = db.add_photo(
                        line_id, result["filename"],
                        prompt_text=result.get("prompt_text"),
                        description=result.get("description"),
                        seed=result.get("seed"),
                        comfy_prompt_id=result.get("prompt_id"),
                        status="done",
                    )
                    generated += 1
                    await ws.send_json({
                        "type": "result",
                        "id": photo_id,
                        "current": i + 1,
                        "total": count,
                        "filename": result["filename"],
                        "url": f"/api/files/results/{result['filename']}",
                        "description": result.get("description", ""),
                    })
                else:
                    await ws.send_json({
                        "type": "error",
                        "current": i + 1,
                        "message": f"Photo #{i+1} failed: {result.get('error', 'unknown')}",
                    })

        await ws.send_json({"type": "done", "total": count, "generated": generated})
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await ws.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass


# ==================== Photo QA ====================

@app.post("/api/photos/{photo_id}/qa")
async def photo_qa(photo_id: int, data: dict):
    status = data.get("status")
    if status not in ("accepted", "rejected", "pending"):
        raise HTTPException(400, "Invalid status")
    db.update_photo(photo_id, qa_status=status)
    return {"ok": True}


@app.post("/api/lines/{line_id}/bulk-qa")
async def bulk_qa(line_id: int, data: dict):
    ids = data.get("ids", [])
    status = data.get("status")
    if status not in ("accepted", "rejected", "pending"):
        raise HTTPException(400, "Invalid status")
    for pid in ids:
        db.update_photo(pid, qa_status=status)
    return {"ok": True, "count": len(ids)}


@app.post("/api/photos/{photo_id}/regenerate")
async def regenerate_photo(photo_id: int):
    """Regenerate a rejected photo with new seed."""
    photo = db.get_photo(photo_id)
    if not photo:
        raise HTTPException(404, "Photo not found")

    line = db.get_line(photo["line_id"])
    if not line or not line.get("photo_ref_path"):
        raise HTTPException(400, "Line has no reference photo")

    # Parse toggles from description (simplified - just regenerate with random)
    ref_path = line["photo_ref_path"]
    loop = asyncio.get_event_loop()

    async with pipeline.gpu_lock:
        gpu_ok = await loop.run_in_executor(None, ensure_gpu_free, "photo")
        if not gpu_ok:
            raise HTTPException(503, "GPU unavailable")

        result = await pipeline.run_photo_variation(ref_path, {"clothing_color": True, "brand_text": True}, 0, False)

    if result["success"]:
        db.update_photo(photo_id,
                        filename=result["filename"],
                        seed=result.get("seed"),
                        description=result.get("description"),
                        prompt_text=result.get("prompt_text"),
                        qa_status="pending",
                        status="done")
        return {"ok": True, "filename": result["filename"],
                "url": f"/api/files/results/{result['filename']}"}
    raise HTTPException(500, "Regeneration failed")


# ==================== Vast.ai ====================

@app.get("/api/vast/status")
async def vast_status():
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, vast_bridge.get_vast_status)


@app.post("/api/vast/stop")
async def vast_stop(data: dict):
    iid = data.get("instance_id")
    if not iid:
        raise HTTPException(400, "instance_id required")
    ok = vast_bridge.stop_instance(int(iid))
    return {"ok": ok}


@app.post("/api/vast/start")
async def vast_start(data: dict):
    iid = data.get("instance_id")
    if not iid:
        raise HTTPException(400, "instance_id required")
    ok = vast_bridge.start_instance(int(iid))
    return {"ok": ok}


# ==================== Video Generation ====================

@app.websocket("/api/lines/{line_id}/generate-videos")
async def ws_generate_videos(ws: WebSocket, line_id: int):
    await ws.accept()
    try:
        config = await ws.receive_json()

        line = db.get_line(line_id)
        if not line:
            await ws.send_json({"type": "error", "message": "Line not found"})
            return

        video_ref = line.get("video_ref_path")
        if not video_ref or not os.path.exists(video_ref):
            await ws.send_json({"type": "error", "message": "No video reference set"})
            return

        # Positive: auto-generate from an accepted photo if the line has none yet.
        positive = (line.get("positive_prompt") or "").strip()
        if not positive:
            try:
                _acc = db.get_photos_by_line(line_id, qa_status="accepted")
            except Exception:
                _acc = []
            _src = None
            for _p in _acc or []:
                _cand = RESULTS_DIR / _p["filename"]
                if _cand.exists():
                    _src = str(_cand)
                    break
            if _src:
                loop = asyncio.get_event_loop()
                positive = await loop.run_in_executor(None, pipeline.describe_video_prompt, _src)
            if not positive:
                positive = pipeline.DEFAULT_VIDEO_POSITIVE
            db.update_line(line_id, positive_prompt=positive)
            await ws.send_json({"type": "status", "message": f"Auto-prompt: {positive[:80]}…"})
        # Negative: always fall back to the baked default if the line has none.
        negative = (line.get("negative_prompt") or "").strip() or pipeline.DEFAULT_VIDEO_NEGATIVE

        # Get accepted photos
        photos = db.get_photos_by_line(line_id, qa_status="accepted")
        if not photos:
            await ws.send_json({"type": "error", "message": "No accepted photos"})
            return

        # Check Vast tunnel
        await ws.send_json({"type": "status", "message": "Checking Vast connection..."})
        status = vast_bridge.get_vast_status()
        if not status["tunnel_ok"]:
            await ws.send_json({"type": "error", "message": "Vast SSH tunnel not connected (localhost:28188)"})
            return

        db.update_line(line_id, status="videos")

        total = len(photos)
        done = 0

        for i, photo in enumerate(photos):
            photo_path = str(RESULTS_DIR / photo["filename"])
            if not os.path.exists(photo_path):
                await ws.send_json({"type": "error", "current": i + 1, "message": f"Photo file missing: {photo['filename']}"})
                continue

            vid_id = db.add_video(line_id, photo["id"], status="generating")

            await ws.send_json({
                "type": "video_progress",
                "video_id": vid_id,
                "photo_id": photo["id"],
                "current": i + 1,
                "total": total,
                "status": "generating",
                "photo_url": f"/api/files/results/{photo['filename']}",
            })

            async def on_progress(msg):
                try:
                    await ws.send_json({
                        "type": "video_progress",
                        "video_id": vid_id,
                        "current": i + 1,
                        "total": total,
                        "status": "generating",
                        "progress_text": msg,
                    })
                    db.update_video(vid_id, progress_text=msg)
                except Exception:
                    pass

            try:
                result = await vast_bridge.generate_video(
                    photo_path, video_ref, positive, negative,
                    on_progress=on_progress
                )

                if result["success"]:
                    db.update_video(vid_id, filename=result["filename"],
                                    comfy_prompt_id=result.get("prompt_id"),
                                    status="done")
                    done += 1
                    await ws.send_json({
                        "type": "video_result",
                        "video_id": vid_id,
                        "current": i + 1,
                        "total": total,
                        "filename": result["filename"],
                        "url": f"/api/files/results/{result['filename']}",
                    })
                else:
                    db.update_video(vid_id, status="failed",
                                    progress_text=result.get("error", "failed"))
                    await ws.send_json({
                        "type": "error",
                        "current": i + 1,
                        "message": f"Video #{i+1} failed: {result.get('error', 'unknown')}",
                    })
            except Exception as e:
                db.update_video(vid_id, status="failed", progress_text=str(e))
                await ws.send_json({
                    "type": "error",
                    "current": i + 1,
                    "message": f"Video #{i+1} error: {str(e)}",
                })

        await ws.send_json({"type": "done", "total": total, "done": done})
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await ws.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass


# ==================== Video QA ====================

@app.post("/api/videos/{video_id}/qa")
async def video_qa(video_id: int, data: dict):
    status = data.get("status")
    if status not in ("accepted", "rejected", "pending"):
        raise HTTPException(400, "Invalid status")
    db.update_video(video_id, qa_status=status)
    return {"ok": True}


# ==================== Drive Upload ====================

@app.websocket("/api/lines/{line_id}/upload-drive")
async def ws_upload_drive(ws: WebSocket, line_id: int):
    await ws.accept()
    try:
        config = await ws.receive_json()

        line = db.get_line(line_id)
        if not line:
            await ws.send_json({"type": "error", "message": "Line not found"})
            return

        folder_id = line.get("drive_folder_id", "").strip()
        if not folder_id:
            await ws.send_json({"type": "error", "message": "No Drive folder ID set"})
            return

        if not drive_bridge.check_credentials():
            await ws.send_json({"type": "error", "message": "Google Drive credentials not found"})
            return

        # Collect accepted photos and videos
        photos = db.get_photos_by_line(line_id, qa_status="accepted")
        videos = [v for v in db.get_videos_by_line(line_id) if v["qa_status"] == "accepted"]

        items = []
        for p in photos:
            path = str(RESULTS_DIR / p["filename"])
            if os.path.exists(path):
                items.append(("photo", p["id"], path, p["filename"]))
        for v in videos:
            if v.get("filename"):
                path = str(RESULTS_DIR / v["filename"])
                if os.path.exists(path):
                    items.append(("video", v["id"], path, v["filename"]))

        if not items:
            await ws.send_json({"type": "error", "message": "No accepted files to upload"})
            return

        total = len(items)
        uploaded = 0

        await ws.send_json({"type": "status", "message": f"Uploading {total} files to Drive..."})

        loop = asyncio.get_event_loop()

        for i, (item_type, item_id, path, filename) in enumerate(items):
            upload_id = db.add_drive_upload(line_id, item_type, item_id)

            await ws.send_json({
                "type": "upload_progress",
                "item_type": item_type,
                "item_id": item_id,
                "current": i + 1,
                "total": total,
                "filename": filename,
            })

            try:
                result = await loop.run_in_executor(
                    None, drive_bridge.upload_file, path, folder_id, filename
                )

                if result["success"]:
                    db.update_drive_upload(upload_id,
                                          status="done",
                                          drive_file_id=result.get("drive_file_id"),
                                          drive_link=result.get("drive_link"))
                    uploaded += 1
                else:
                    db.update_drive_upload(upload_id, status="failed",
                                          error=result.get("error"))
                    await ws.send_json({
                        "type": "error",
                        "current": i + 1,
                        "message": f"Upload failed: {result.get('error', 'unknown')}",
                    })
            except Exception as e:
                db.update_drive_upload(upload_id, status="failed", error=str(e))
                await ws.send_json({
                    "type": "error",
                    "current": i + 1,
                    "message": f"Upload error: {str(e)}",
                })

        folder_link = drive_bridge.get_folder_link(folder_id)
        db.update_line(line_id, status="done")

        await ws.send_json({
            "type": "upload_done",
            "total": total,
            "uploaded": uploaded,
            "folder_link": folder_link,
        })
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await ws.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass


# ==================== Word Categories (for toggles UI) ====================

@app.get("/api/words")
async def get_words():
    from brand_words import CATEGORIES
    return CATEGORIES


# ==================== Static HTML pages ====================

@app.get("/line")
@app.get("/line.html")
async def line_page():
    return FileResponse(str(BASE_DIR / "static" / "line.html"))


@app.get("/settings")
@app.get("/settings.html")
async def settings_page():
    return FileResponse(str(BASE_DIR / "static" / "settings.html"))


# Mount static files last
app.mount("/", StaticFiles(directory=str(BASE_DIR / "static"), html=True), name="static")
