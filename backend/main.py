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

class TimeSync(BaseModel):
    timestamp_ms: int


def normalize_config(payload: dict) -> dict:
    # support config files made before the sleep key was renamed
    for preset in payload.get("presets", []):
        if "sleep" not in preset and "sleep_sec" in preset:
            preset["sleep"] = preset["sleep_sec"]
        preset.pop("sleep_sec", None)
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

@app.get("/api/status")
def get_status():
    global sensor_process
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
    return {"status": "success", "is_running": is_running}

@app.post("/api/start-sensor")
def start_sensor():
    global sensor_process
    try:
        if sensor_process and sensor_process.poll() is None:
            return {"status": "error", "message": "Sensor is already running"}
            
        with open("/tmp/bme_log.txt", "w") as f:
            f.write("Starting sensor...\n")
            
        log_file = open("/tmp/bme_log.txt", "a")
        
        sensor_process = subprocess.Popen(
            ["./main"], 
            cwd=SENSOR_DIR,
            stdout=log_file,
            stderr=subprocess.STDOUT
        )
        return {"status": "success", "pid": sensor_process.pid}
    except Exception as e:
        with open("/tmp/bme_log.txt", "a") as f:
            f.write(f"Failed to start: {str(e)}\n")
        return {"status": "error", "message": str(e)}

@app.post("/api/stop-sensor")
def stop_sensor():
    global sensor_process
    try:
        if sensor_process and sensor_process.poll() is None:
            sensor_process.terminate()
            sensor_process.wait(timeout=2)
            sensor_process = None
        else:
            subprocess.run(["pkill", "-x", "main"])
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
