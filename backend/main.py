from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import subprocess
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Literal, Optional

app = FastAPI()

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
INTERFACE_DIR = PROJECT_DIR / "interface"
SENSOR_DIR = PROJECT_DIR / "bme-code"

# saved config survives reboot
CONFIG_PATH = Path(os.environ.get("BME_CONFIG_PATH", "/var/lib/bme688/config.json"))
LEGACY_CONFIG_PATH = Path("/tmp/bme_config.json")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

sensor_process = None
sensor_file_started_at = None
sensor_file_duration_minutes = None
FILE_DURATION_OPTIONS = {15, 20, 25, 30}
DEFAULT_FILE_DURATION_MINUTES = 30

class TimeSync(BaseModel):
    timestamp_ms: int


class StartSensorRequest(BaseModel):
    file_duration_minutes: Literal[15, 20, 25, 30] = DEFAULT_FILE_DURATION_MINUTES


def normalize_config(payload: dict) -> dict:
    # support config files made before the sleep key was renamed
    for preset in payload.get("presets", []):
        if "sleep" not in preset and "sleep_sec" in preset:
            preset["sleep"] = preset["sleep_sec"]
        preset.pop("sleep_sec", None)
    if payload.get("file_duration_minutes") not in FILE_DURATION_OPTIONS:
        payload["file_duration_minutes"] = DEFAULT_FILE_DURATION_MINUTES
    return payload


def write_config(payload: dict) -> None:
    # replace the old file only after the new file is complete
    payload = normalize_config(payload)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=CONFIG_PATH.parent,
            prefix=f".{CONFIG_PATH.name}.",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            json.dump(payload, temp_file)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, CONFIG_PATH)
        try:
            directory_fd = os.open(CONFIG_PATH.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            # directory sync is not supported on every filesystem
            pass
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()


def read_config() -> dict | None:
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("r", encoding="utf-8") as config_file:
            payload = normalize_config(json.load(config_file))
        return payload

    if LEGACY_CONFIG_PATH.exists():
        with LEGACY_CONFIG_PATH.open("r", encoding="utf-8") as legacy_file:
            payload = normalize_config(json.load(legacy_file))
        write_config(payload)
        return payload

    return None


def read_file_duration() -> int:
    payload = read_config()
    if payload is None:
        return DEFAULT_FILE_DURATION_MINUTES
    return payload["file_duration_minutes"]


def write_file_duration(file_duration_minutes: int) -> None:
    payload = read_config() or {}
    payload["file_duration_minutes"] = file_duration_minutes
    write_config(payload)

@app.post("/api/sync-time")
def sync_time(data: TimeSync):
    timestamp_sec = data.timestamp_ms / 1000.0
    try:
        subprocess.run(["sudo", "date", "-s", f"@{timestamp_sec}"], check=True)
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/config")
def save_config(payload: dict):
    try:
        if "file_duration_minutes" not in payload:
            payload["file_duration_minutes"] = read_file_duration()
        write_config(payload)
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/config")
def get_config():
    try:
        payload = read_config()
        if payload is not None:
            return payload
        return {"status": "error", "message": "No config"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/file-duration")
def get_file_duration():
    try:
        return {
            "status": "success",
            "file_duration_minutes": read_file_duration(),
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/file-duration")
def save_file_duration(request: StartSensorRequest):
    try:
        write_file_duration(request.file_duration_minutes)
        return {
            "status": "success",
            "file_duration_minutes": request.file_duration_minutes,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/status")
def get_status():
    global sensor_process, sensor_file_started_at, sensor_file_duration_minutes
    is_running = False
    if sensor_process and sensor_process.poll() is None:
        is_running = True
    else:
        # uvicorn may have restarted while the sensor kept running
        try:
            subprocess.check_output(["pgrep", "-x", "main"])
            is_running = True
        except subprocess.CalledProcessError:
            pass
    if not is_running:
        sensor_file_started_at = None
        sensor_file_duration_minutes = None
    elif sensor_file_duration_minutes is None:
        sensor_file_duration_minutes = read_file_duration()

    file_remaining_seconds = None
    if is_running and sensor_file_started_at is not None and sensor_file_duration_minutes is not None:
        file_duration_seconds = sensor_file_duration_minutes * 60
        elapsed = time.monotonic() - sensor_file_started_at
        file_remaining_seconds = max(0, round(file_duration_seconds - elapsed % file_duration_seconds))

    return {
        "status": "success",
        "is_running": is_running,
        "file_duration_minutes": sensor_file_duration_minutes,
        "file_remaining_seconds": file_remaining_seconds,
    }

@app.post("/api/start-sensor")
def start_sensor(request: Optional[StartSensorRequest] = None):
    global sensor_process, sensor_file_started_at, sensor_file_duration_minutes
    try:
        if sensor_process and sensor_process.poll() is None:
            return {"status": "error", "message": "Sensor is already running"}

        file_duration_minutes = (
            request.file_duration_minutes if request else read_file_duration()
        )
        write_file_duration(file_duration_minutes)
            
        with open("/tmp/bme_log.txt", "w") as f:
            f.write("Starting sensor...\n")
            
        with open("/tmp/bme_log.txt", "a") as log_file:
            sensor_process = subprocess.Popen(
                ["./main", str(file_duration_minutes)],
                cwd=SENSOR_DIR,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )

        sensor_file_started_at = time.monotonic()
        sensor_file_duration_minutes = file_duration_minutes
        return {
            "status": "success",
            "pid": sensor_process.pid,
            "file_duration_minutes": file_duration_minutes,
        }
    except Exception as e:
        with open("/tmp/bme_log.txt", "a") as f:
            f.write(f"Failed to start: {str(e)}\n")
        return {"status": "error", "message": str(e)}

@app.post("/api/stop-sensor")
def stop_sensor():
    global sensor_process, sensor_file_started_at, sensor_file_duration_minutes
    try:
        if sensor_process and sensor_process.poll() is None:
            sensor_process.terminate()
            sensor_process.wait(timeout=2)
            sensor_process = None
        else:
            subprocess.run(["pkill", "-x", "main"])
        sensor_file_started_at = None
        sensor_file_duration_minutes = None
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/latest")
def get_latest():
    try:
        global sensor_process
        if sensor_process and sensor_process.poll() is not None:
            return {"status": "error", "message": "Sensor process crashed."}

        if not os.path.exists("/tmp/bme_latest.json"):
            return {"status": "error", "message": "Waiting for sensor data..."}
            
        with open("/tmp/bme_latest.json", "r") as f:
            data = json.load(f)
        return {"status": "success", "data": data}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/logs")
def get_logs():
    try:
        if not os.path.exists("/tmp/bme_log.txt"):
            return {"status": "success", "logs": "No logs yet."}
        
        with open("/tmp/bme_log.txt", "r") as f:
            lines = f.readlines()
        tail = "".join(lines[-20:])
        return {"status": "success", "logs": tail}
    except Exception as e:
        return {"status": "error", "message": str(e)}

app.mount("/static", StaticFiles(directory=INTERFACE_DIR, html=True), name="static")

@app.get("/generate_204")
@app.get("/hotspot-detect.html")
@app.get("/")
def captive_portal():
    return RedirectResponse(url="/static/index.html")
