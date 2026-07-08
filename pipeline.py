#!/usr/bin/env python3
"""
Content Pipeline — core generation logic.
Faceswap, clean, and photo variations against a remote ComfyUI instance.
"""

import asyncio, copy, json, random, subprocess, time, uuid, os
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

from brand_words import ALL_WORDS, CATEGORIES
from gpu_guard import ensure_gpu_free

BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
RESULTS_DIR = BASE_DIR / "results"
# Remote ComfyUI endpoint (override via env COMFY_HOST).
COMFYUI_URL = os.environ.get("COMFY_HOST", "https://comfyui.example.com")
FACESWAP_PROMPT_PATH = BASE_DIR / "base_prompt_faceswap.json"
PHOTO_EDIT_PROMPT_PATH = BASE_DIR / "base_prompt.json"

# GPU lock — serialize all GPU operations
gpu_lock = asyncio.Lock()

# ---------- Auto video-prompt (Gemini vision) ----------
# Default negative prompt for Wan i2v — applied to every line silently.
DEFAULT_VIDEO_NEGATIVE = (
    "low quality, blurry, distorted, deformed, bad anatomy, extra limbs, "
    "fused fingers, mutated hands, watermark, text, logo, jpeg artifacts, "
    "oversaturated, flickering, frozen, static, duplicate, glitch"
)
# Fallback positive if Gemini refuses or fails — keeps the user unblocked.
DEFAULT_VIDEO_POSITIVE = (
    "a young woman, natural soft lighting, subtle natural body movement, "
    "gentle hair motion, looking toward camera, smooth realistic motion, "
    "cinematic, high quality, 4k"
)

# 2.5 only. flash is primary, flash-lite is the fallback (separate quota) on 429.
GEMINI_MODELS = ["gemini-2.5-flash", "gemini-2.5-flash-lite"]


def _gemini_api_keys() -> list:
    """Load all available Gemini keys (env + panel .env). Rotated on 429."""
    keys = []
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("GEMINI_API_KEYS="):
                keys += [k.strip() for k in line.split("=", 1)[1].split(",") if k.strip()]
            elif line.startswith("GEMINI_API_KEY="):
                keys.append(line.split("=", 1)[1].strip())
    for var in ("GEMINI_API_KEY", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3"):
        v = os.environ.get(var)
        if v:
            keys.append(v.strip())
    # de-dup, preserve order
    seen, out = set(), []
    for k in keys:
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _gemini_api_key() -> Optional[str]:
    keys = _gemini_api_keys()
    return keys[0] if keys else None


_VIDEO_PROMPT_INSTRUCTION = (
    "You are writing a positive prompt for an image-to-video model (Wan 2.2). "
    "Look at the attached photo and describe it as a concise English video prompt: "
    "the subject, their appearance and outfit, the setting/background, the lighting, "
    "and add natural subtle motion (e.g. gentle movement, soft hair motion, breathing, "
    "looking at camera). This is for neutral animation of a still image, describe only "
    "what is visibly present in a factual way. End with 'cinematic, high quality, 4k'. "
    "Output ONLY the prompt as one comma-separated line, no preamble, no quotes, no explanation."
)


def describe_video_prompt(image_path: str) -> str:
    """Use Gemini vision to turn a reference photo into a Wan i2v positive prompt.

    Returns the prompt string, or "" on failure/refusal (caller falls back to default).
    """
    import base64, urllib.request, urllib.error

    keys = _gemini_api_keys()
    if not keys or not os.path.exists(image_path):
        return ""

    ext = os.path.splitext(image_path)[1].lower()
    mime = "image/png" if ext == ".png" else "image/jpeg"
    try:
        b64 = base64.b64encode(Path(image_path).read_bytes()).decode()
    except Exception:
        return ""

    def _build_body(model: str) -> bytes:
        gen = {"temperature": 0.4, "maxOutputTokens": 300}
        # 2.5 models "think" by default and that eats the output budget — disable it.
        if "2.5" in model:
            gen["thinkingConfig"] = {"thinkingBudget": 0}
        return json.dumps({
            "contents": [{
                "parts": [
                    {"text": _VIDEO_PROMPT_INSTRUCTION},
                    {"inline_data": {"mime_type": mime, "data": b64}},
                ]
            }],
            "generationConfig": gen,
        }).encode()

    # Try each model; rotate through all keys on any per-key error (quota/invalid).
    for model in GEMINI_MODELS:
        body = _build_body(model)
        for key in keys:
            url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
                   f"{model}:generateContent?key={key}")
            try:
                req = urllib.request.Request(
                    url, data=body, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=40) as resp:
                    data = json.loads(resp.read().decode())
            except Exception:
                continue  # quota / invalid key / network — try next key
            cands = data.get("candidates") or []
            if not cands:
                continue
            parts = cands[0].get("content", {}).get("parts", [])
            text = " ".join(p.get("text", "") for p in parts).strip()
            text = text.strip().strip('"').strip()
            if text and len(text) > 15:
                return text
    return ""

# ---------- Variation pools ----------

CLOTHING_COLORS = [
    "bright red", "jet black", "emerald green", "deep navy blue", "hot pink",
    "burnt orange", "snow white", "sunny yellow", "teal", "burgundy",
    "lavender purple", "coral", "olive green", "dusty rose", "charcoal grey",
]
HAIR_SHADES = [
    "warm honey blonde highlights", "dark auburn with copper tones",
    "rich chocolate brown with caramel highlights", "soft ash brown with platinum streaks",
    "warm chestnut brown", "dark brown with reddish undertones",
    "light golden brown with sun-kissed tips", "deep espresso brown",
    "warm brown with golden highlights", "dark brown with subtle burgundy tint",
    "sandy brown with light streaks", "mahogany brown",
]
OUTFITS = [
    "elegant red evening dress with thin straps", "casual white oversized t-shirt and denim shorts",
    "black leather jacket over a crop top", "cozy beige knit sweater",
    "floral summer dress with sunflower pattern", "sporty pink hoodie and leggings",
    "classic blue denim jacket over white tank top", "silk emerald green blouse with rolled sleeves",
    "vintage yellow polka dot dress", "stylish grey blazer over black turtleneck",
    "off-shoulder lavender maxi dress", "striped navy sailor top",
    "bright orange crop top and high-waisted jeans", "elegant black cocktail dress",
    "pastel pink satin slip dress", "white lace blouse with pearl buttons",
]
TOYS = [
    ("a cute white teddy bear with a red bow tie", "snow white"),
    ("a fluffy blue stuffed elephant with big floppy ears", "sky blue"),
    ("a soft orange fox plush with a bushy tail", "bright orange"),
    ("a yellow rubber duck toy with an orange beak", "sunny yellow"),
    ("a purple stuffed unicorn with a shiny golden horn", "lavender purple"),
    ("a green stuffed dinosaur with tiny arms", "bright green"),
    ("a red stuffed heart-shaped pillow with lace edges", "cherry red"),
    ("a turquoise stuffed cat with big round eyes", "turquoise"),
    ("a coral pink stuffed flamingo standing on one leg", "coral pink"),
    ("a brown stuffed puppy dog with floppy ears", "chocolate brown"),
    ("a mint green stuffed frog with big eyes", "mint green"),
    ("a golden stuffed lion with a fluffy mane", "golden"),
]
TOY_COLORS = [
    "bright blue", "vivid green", "sunny yellow", "deep purple", "bright orange",
    "snow white", "cherry red", "turquoise", "golden yellow", "hot pink",
    "lavender", "mint green", "coral", "teal", "burgundy",
]
INTERIOR_DETAILS = [
    "light beige walls with a small green potted plant on a shelf",
    "soft blue walls with a white air conditioner unit on the wall",
    "warm yellow walls with a framed abstract painting",
    "light grey walls with floating wooden shelves and books",
    "pastel pink walls with fairy lights string",
    "white walls with a large round mirror",
    "cream walls with sheer white curtains on the window",
    "light green walls with a small cactus on the windowsill",
    "warm terracotta walls with a woven wall hanging",
    "soft lavender walls with a modern minimalist clock",
    "beige walls with a vintage poster and a coat rack",
    "white brick walls with hanging ivy plants",
]


def build_prompt_text(toggles: dict, variation_index: int) -> tuple[str, str]:
    """Build Qwen prompt from active toggles. Returns (prompt_text, description)."""
    parts = []
    desc_parts = []

    if toggles.get("clothing_color"):
        color = random.choice(CLOTHING_COLORS)
        parts.append(f"Change the color of the clothing to {color}.")
        desc_parts.append(f"color: {color}")

    if toggles.get("hair_shade"):
        shade = random.choice(HAIR_SHADES)
        parts.append(f"Change the hair color to {shade}, keep the same hairstyle and length.")
        desc_parts.append(f"hair: {shade[:25]}")

    if toggles.get("brand_text"):
        word = random.choice(ALL_WORDS)
        parts.append(f'Change the text on the clothing to "{word}".')
        desc_parts.append(f'text: "{word}"')

    if toggles.get("outfit_change"):
        outfit = random.choice(OUTFITS)
        parts.append(f"Change the clothing to {outfit}.")
        desc_parts.append(f"outfit: {outfit[:25]}")

    if toggles.get("toy_replace"):
        toy_desc, toy_color = random.choice(TOYS)
        parts.append(f"Replace the plush toy with {toy_desc} in {toy_color} color.")
        desc_parts.append(f"toy: {toy_desc[:20]}")

    if toggles.get("toy_color"):
        tc = random.choice(TOY_COLORS)
        parts.append(f"Change the color of the plush toy to {tc}.")
        desc_parts.append(f"toy color: {tc}")

    if toggles.get("interior_details"):
        detail = random.choice(INTERIOR_DETAILS)
        parts.append(f"Change the background wall and room details to {detail}.")
        desc_parts.append(f"interior: {detail[:25]}")

    # Preservation
    preserve = []
    if not toggles.get("outfit_change"):
        preserve.append("same clothing style and design")
    if not toggles.get("clothing_color") and not toggles.get("outfit_change"):
        preserve.append("same clothing color")
    if not toggles.get("hair_shade"):
        preserve.append("same hair color and style")
    if not toggles.get("toy_replace") and not toggles.get("toy_color"):
        preserve.append("same toy")
    if not toggles.get("interior_details"):
        preserve.append("same background")

    parts.append(f"Keep everything else exactly the same - same person, same face, same pose, same expression, {', '.join(preserve)}.")
    parts.append("High quality, photorealistic, 4k")

    prompt_text = " ".join(parts)
    description = " | ".join(desc_parts) if desc_parts else "no changes"
    return prompt_text, description


def check_detail(path: str) -> bool:
    """Optional QA: verify two sampled regions differ enough (detail preserved)."""
    try:
        img = np.array(Image.open(path))
        ih, iw = img.shape[:2]
        region_a = img[int(ih*0.44):int(ih*0.50), int(iw*0.30):int(iw*0.42)].mean(axis=(0,1))[:3]
        region_b = img[int(ih*0.50):int(ih*0.58), int(iw*0.10):int(iw*0.25)].mean(axis=(0,1))[:3]
        return float(np.linalg.norm(region_a - region_b)) > 20
    except Exception:
        return True


# ---------- ComfyUI helpers ----------

def _upload_to_comfyui(local_path: str, comfy_name: str) -> bool:
    """Upload image to ComfyUI input folder with uuid prefix."""
    try:
        r = subprocess.run(
            ["curl", "-s", "-X", "POST", f"{COMFYUI_URL}/upload/image",
             "-F", f"image=@{local_path};filename={comfy_name}",
             "-F", "subfolder=", "-F", "type=input"],
            capture_output=True, text=True, timeout=60
        )
        return r.returncode == 0
    except Exception:
        return False


def _submit_prompt(data: dict) -> Optional[str]:
    """Submit prompt to ComfyUI via -d payload (not @file). Returns prompt_id."""
    payload = json.dumps(data)
    try:
        r = subprocess.run(
            ["curl", "-s", "-X", "POST", f"{COMFYUI_URL}/prompt",
             "-H", "Content-Type: application/json",
             "-d", payload],
            capture_output=True, text=True, timeout=30
        )
        return json.loads(r.stdout).get("prompt_id")
    except Exception:
        return None


def _wait_for_result(prompt_id: str, timeout: int = 300) -> Optional[str]:
    """Wait for ComfyUI to finish, return output filename."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = subprocess.run(
                ["curl", "-s", f"{COMFYUI_URL}/history/{prompt_id}"],
                capture_output=True, text=True, timeout=15
            )
            data = json.loads(r.stdout)
            if prompt_id in data:
                status = data[prompt_id]["status"]["status_str"]
                if status == "success":
                    for nid, out in data[prompt_id]["outputs"].items():
                        if "images" in out:
                            for img in out["images"]:
                                fn = img["filename"]
                                if "HeadSwap_3" not in fn and "Sigma" not in fn:
                                    return fn
                elif status == "error":
                    return None
        except Exception:
            pass
        time.sleep(3)
    return None


def _download_result(filename: str, output_path: str) -> bool:
    """Download result from ComfyUI output."""
    try:
        subprocess.run(
            ["curl", "-s", "-o", output_path,
             f"{COMFYUI_URL}/view?filename={filename}&type=output"],
            timeout=60
        )
        return os.path.exists(output_path) and os.path.getsize(output_path) > 1000
    except Exception:
        return False


# ---------- Pipeline operations ----------

async def run_faceswap(body_image_path: str, face_ref_name: str = "reference_face.jpg",
                       seed: int | None = None) -> dict:
    """
    Run faceswap: body_image + face_ref -> faceswapped result.
    Returns {"success": bool, "filename": str, "output_path": str, "prompt_id": str, "seed": int}
    """
    loop = asyncio.get_event_loop()

    # Upload body image to ComfyUI with uuid prefix
    body_basename = os.path.basename(body_image_path)
    comfy_body_name = f"{uuid.uuid4().hex[:8]}_{body_basename}"
    await loop.run_in_executor(None, _upload_to_comfyui, body_image_path, comfy_body_name)

    # Upload face ref
    face_path = str(UPLOAD_DIR / "faces" / face_ref_name)
    comfy_face_name = f"{uuid.uuid4().hex[:8]}_{face_ref_name}"
    if os.path.exists(face_path):
        await loop.run_in_executor(None, _upload_to_comfyui, face_path, comfy_face_name)

    # Load faceswap workflow
    with open(FACESWAP_PROMPT_PATH) as f:
        base = json.load(f)

    seed = seed or random.randint(1, 999999999)

    data = copy.deepcopy(base)
    data["prompt"]["343"]["inputs"]["image"] = comfy_body_name  # body
    data["prompt"]["349"]["inputs"]["image"] = comfy_face_name  # face ref
    data["prompt"]["395"]["inputs"]["noise_seed"] = seed

    pid = await loop.run_in_executor(None, _submit_prompt, data)
    if not pid:
        return {"success": False, "error": "Failed to submit prompt"}

    fn = await loop.run_in_executor(None, _wait_for_result, pid, 300)
    if not fn:
        return {"success": False, "error": "Generation timed out or failed", "prompt_id": pid}

    out_name = f"faceswap_{uuid.uuid4().hex[:8]}.png"
    out_path = str(RESULTS_DIR / out_name)
    ok = await loop.run_in_executor(None, _download_result, fn, out_path)

    return {
        "success": ok,
        "filename": out_name if ok else None,
        "output_path": out_path if ok else None,
        "prompt_id": pid,
        "seed": seed,
    }


async def run_clean(source_image_path: str,
                    clean_prompt: str = "Remove all logos, text, and watermarks from the image. Keep everything else exactly the same - same person, same pose, same clothes, same background. High quality, photorealistic, 4k.",
                    seed: int | None = None) -> dict:
    """
    Clean source image (remove logos/text).
    Returns {"success": bool, "filename": str, "output_path": str, "prompt_id": str, "seed": int}
    """
    loop = asyncio.get_event_loop()

    basename = os.path.basename(source_image_path)
    comfy_name = f"{uuid.uuid4().hex[:8]}_{basename}"
    await loop.run_in_executor(None, _upload_to_comfyui, source_image_path, comfy_name)

    with open(PHOTO_EDIT_PROMPT_PATH) as f:
        base = json.load(f)

    seed = seed or random.randint(1, 999999999)

    data = copy.deepcopy(base)
    # Both image slots get the same source (image1=source, image2=reference=same)
    data["prompt"]["343"]["inputs"]["image"] = comfy_name
    data["prompt"]["349"]["inputs"]["image"] = comfy_name
    data["prompt"]["395"]["inputs"]["noise_seed"] = seed
    data["prompt"]["348"]["inputs"]["prompt"] = clean_prompt

    pid = await loop.run_in_executor(None, _submit_prompt, data)
    if not pid:
        return {"success": False, "error": "Failed to submit prompt"}

    fn = await loop.run_in_executor(None, _wait_for_result, pid, 300)
    if not fn:
        return {"success": False, "error": "Generation timed out or failed", "prompt_id": pid}

    out_name = f"clean_{uuid.uuid4().hex[:8]}.png"
    out_path = str(RESULTS_DIR / out_name)
    ok = await loop.run_in_executor(None, _download_result, fn, out_path)

    return {
        "success": ok,
        "filename": out_name if ok else None,
        "output_path": out_path if ok else None,
        "prompt_id": pid,
        "seed": seed,
    }


async def run_photo_variation(reference_image_path: str, toggles: dict,
                              variation_index: int = 0, verify_detail: bool = False,
                              seed: int | None = None) -> dict:
    """
    Generate one photo variation from reference.
    Returns {"success": bool, "filename": str, "description": str, "prompt_id": str, "seed": int}
    """
    loop = asyncio.get_event_loop()

    basename = os.path.basename(reference_image_path)
    comfy_name = f"{uuid.uuid4().hex[:8]}_{basename}"
    await loop.run_in_executor(None, _upload_to_comfyui, reference_image_path, comfy_name)

    with open(PHOTO_EDIT_PROMPT_PATH) as f:
        base = json.load(f)

    prompt_text, description = build_prompt_text(toggles, variation_index)

    max_retries = 4 if verify_detail else 1
    for attempt in range(max_retries):
        seed_val = seed if (seed and attempt == 0) else random.randint(1, 999999999)

        data = copy.deepcopy(base)
        data["prompt"]["343"]["inputs"]["image"] = comfy_name
        data["prompt"]["349"]["inputs"]["image"] = comfy_name
        data["prompt"]["395"]["inputs"]["noise_seed"] = seed_val
        data["prompt"]["348"]["inputs"]["prompt"] = prompt_text

        pid = await loop.run_in_executor(None, _submit_prompt, data)
        if not pid:
            continue

        fn = await loop.run_in_executor(None, _wait_for_result, pid, 300)
        if not fn:
            continue

        out_name = f"gen_{uuid.uuid4().hex[:8]}.png"
        out_path = str(RESULTS_DIR / out_name)
        ok = await loop.run_in_executor(None, _download_result, fn, out_path)
        if not ok:
            continue

        if not verify_detail or await loop.run_in_executor(None, check_detail, out_path):
            return {
                "success": True,
                "filename": out_name,
                "output_path": out_path,
                "description": description,
                "prompt_text": prompt_text,
                "prompt_id": pid,
                "seed": seed_val,
            }

    return {
        "success": False,
        "error": "All attempts failed",
        "description": description,
        "prompt_text": prompt_text,
    }
