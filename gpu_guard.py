#!/usr/bin/env python3
"""
GPU Guard — safe access to the GPU on the remote worker machine.

Serializes GPU work and protects the card: availability checks, VRAM/temperature
limits, model unloading, and cool-down waits. All generation operations acquire a
process-wide lock (see pipeline.gpu_lock) and then call ensure_gpu_free() before
touching the GPU.

Configuration (environment variables):
  WORKER_SSH        SSH target for the worker, e.g. "user@10.0.0.5" (required for
                    remote control; if unset, GPU control is a no-op).
  WORKER_SSH_KEY    Path to the SSH private key (optional).
  COMFY_HOST        ComfyUI base URL, e.g. "https://comfyui.example.com".
  WORKER_COMFY_DIR  ComfyUI install dir on the worker (for restart), optional.
"""

import os, subprocess, time, sys

WORKER_SSH = os.environ.get("WORKER_SSH", "")            # "user@host"
WORKER_SSH_KEY = os.environ.get("WORKER_SSH_KEY", "")    # path to private key
COMFYUI_URL = os.environ.get("COMFY_HOST", "https://comfyui.example.com")
WORKER_COMFY_DIR = os.environ.get("WORKER_COMFY_DIR", r"C:\comfyui")

# Safety limits
MAX_VRAM_IDLE_MB = 8000        # Idle VRAM above this means models are still resident
MAX_TEMP_START = 80            # Do not start work above this temperature (deg C)
MAX_TEMP_RUNNING = 75          # Warn/stop if we exceed this while running
MAX_VRAM_FOR_WAN = 28000       # Wan Animate ceiling (headroom on a 32GB card)
COOL_DOWN_WAIT = 120           # Seconds to wait when overheating


def _ssh_cmd() -> list:
    cmd = ["ssh", "-o", "ConnectTimeout=45", "-o", "ServerAliveInterval=10"]
    if WORKER_SSH_KEY:
        cmd += ["-i", WORKER_SSH_KEY]
    cmd.append(WORKER_SSH)
    return cmd


def ssh(cmd, timeout=60):
    """Run a command on the worker over SSH with graceful error handling."""
    if not WORKER_SSH:
        return "", -3  # remote control disabled
    try:
        r = subprocess.run(_ssh_cmd() + [cmd], capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip(), r.returncode
    except subprocess.TimeoutExpired:
        return "", -1
    except Exception as e:
        return str(e), -2


def get_gpu_stats():
    """Return a dict of GPU stats, or None on error."""
    out, rc = ssh("nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu,"
                  "temperature.gpu,power.draw --format=csv,noheader")
    if rc != 0 or not out:
        return None
    try:
        parts = [p.strip() for p in out.split(",")]
        return {
            "mem_used": int(parts[0].split()[0]),
            "mem_total": int(parts[1].split()[0]),
            "util": int(parts[2].split()[0]),
            "temp": int(parts[3]),
            "power": float(parts[4].split()[0]),
        }
    except (IndexError, ValueError):
        return None


def check_pc_alive():
    """Check whether the worker is reachable."""
    gpu = get_gpu_stats()
    if gpu is None:
        print("[GPU Guard] Worker UNREACHABLE — SSH timeout or tunnel down")
        return False
    print(f"[GPU Guard] Worker up: VRAM {gpu['mem_used']}/{gpu['mem_total']}MB, "
          f"{gpu['util']}% util, {gpu['temp']}C, {gpu['power']}W")
    return True


def free_comfyui_models():
    """Unload all models from VRAM via the ComfyUI API."""
    print("[GPU Guard] Unloading models from ComfyUI VRAM...")
    try:
        subprocess.run(
            ["curl", "-s", "-X", "POST", f"{COMFYUI_URL}/free",
             "-H", "Content-Type: application/json",
             "-d", '{"unload_models": true, "free_memory": true}'],
            capture_output=True, timeout=15
        )
        time.sleep(3)
        print("[GPU Guard] Models unloaded")
        return True
    except Exception:
        print("[GPU Guard] Could not unload models (ComfyUI unreachable?)")
        return False


def restart_comfyui():
    """Restart ComfyUI on a Windows worker as a last resort to free VRAM."""
    comfy = WORKER_COMFY_DIR
    ssh('powershell -Command "Get-Process python -ErrorAction SilentlyContinue | '
        'Stop-Process -Force; Start-Sleep 3; '
        f'Start-Process -FilePath \'{comfy}\\venv\\Scripts\\python.exe\' '
        f'-ArgumentList \'{comfy}\\main.py\',\'--listen\',\'127.0.0.1\',\'--port\',\'8188\','
        f'\'--disable-smart-memory\' -WorkingDirectory \'{comfy}\\\'"')
    time.sleep(15)


def wait_for_cool_gpu(target_temp=45, max_wait=300):
    """Wait until the GPU cools to target_temp."""
    start = time.time()
    while time.time() - start < max_wait:
        gpu = get_gpu_stats()
        if gpu is None:
            print("[GPU Guard] Worker unreachable while waiting to cool down")
            return False
        if gpu['temp'] <= target_temp:
            print(f"[GPU Guard] GPU cooled: {gpu['temp']}C")
            return True
        print(f"[GPU Guard] Cooling: {gpu['temp']}C (target: {target_temp}C)...")
        time.sleep(15)
    print(f"[GPU Guard] Cool-down timeout after {max_wait}s")
    return False


def ensure_gpu_free(mode="photo"):
    """
    Prepare the GPU for work.
    mode="photo" — image edit (light load, ~20-25GB VRAM)
    mode="video" — Wan Animate (heavy, ~28-31GB VRAM, needs a clean GPU)
    """
    print(f"[GPU Guard] Mode: {mode}")

    # 1. Worker reachable?
    gpu = get_gpu_stats()
    if gpu is None:
        print("[GPU Guard] Worker UNREACHABLE")
        return False

    print(f"[GPU Guard] VRAM: {gpu['mem_used']}/{gpu['mem_total']}MB, "
          f"Util: {gpu['util']}%, Temp: {gpu['temp']}C, Power: {gpu['power']}W")

    # 2. Temperature
    if gpu['temp'] > MAX_TEMP_START:
        print(f"[GPU Guard] GPU hot ({gpu['temp']}C > {MAX_TEMP_START}C), cooling down...")
        if not wait_for_cool_gpu(target_temp=40):
            return False

    # 3. Video mode — free VRAM from any resident models
    if mode == "video":
        gpu = get_gpu_stats()
        if gpu and gpu['mem_used'] > MAX_VRAM_IDLE_MB:
            print(f"[GPU Guard] VRAM {gpu['mem_used']}MB > {MAX_VRAM_IDLE_MB}MB — "
                  "models resident, unloading...")
            free_comfyui_models()
            time.sleep(5)
            gpu = get_gpu_stats()
            if gpu:
                print(f"[GPU Guard] After unload: VRAM {gpu['mem_used']}MB")
                if gpu['mem_used'] > MAX_VRAM_IDLE_MB:
                    print(f"[GPU Guard] Still {gpu['mem_used']}MB — restarting ComfyUI...")
                    restart_comfyui()
                    gpu = get_gpu_stats()
                    if gpu:
                        print(f"[GPU Guard] After ComfyUI restart: VRAM {gpu['mem_used']}MB")

    # 4. Wait if the GPU is busy (another session)
    gpu = get_gpu_stats()
    if gpu and gpu['util'] > 50:
        print(f"[GPU Guard] GPU busy ({gpu['util']}%), waiting...")
        for _ in range(60):
            time.sleep(30)
            gpu = get_gpu_stats()
            if gpu is None:
                return False
            if gpu['util'] <= 10:
                break
            print(f"[GPU Guard] Waiting: {gpu['util']}% util, {gpu['temp']}C")
        else:
            print("[GPU Guard] GPU did not free up within 30 min")
            return False

    # 5. Final check
    gpu = get_gpu_stats()
    if gpu is None:
        return False

    if mode == "video" and gpu['mem_used'] > MAX_VRAM_IDLE_MB:
        print(f"[GPU Guard] ABORT: VRAM {gpu['mem_used']}MB > {MAX_VRAM_IDLE_MB}MB "
              "for video mode. Too risky.")
        return False

    print(f"[GPU Guard] GPU READY: VRAM {gpu['mem_used']}MB, "
          f"{gpu['util']}% util, {gpu['temp']}C")
    return True


def monitor_during_generation():
    """Monitor the GPU during generation. Returns stats, or None on SSH error."""
    gpu = get_gpu_stats()
    if gpu is None:
        return None
    if gpu['temp'] > MAX_TEMP_RUNNING:
        print(f"[GPU Monitor] WARNING: GPU {gpu['temp']}C > {MAX_TEMP_RUNNING}C!")
    return gpu


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "photo"
    ok = ensure_gpu_free(mode=mode)
    print("\nGPU ready" if ok else "\nGPU NOT ready")
    sys.exit(0 if ok else 1)
