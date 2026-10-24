from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.background import BackgroundTask
import subprocess
import json
import os
import re
from pathlib import Path
import tempfile
import threading
import time
from typing import Literal, Optional
import unicodedata
from datetime import datetime
import zipfile

try:
    import classifier
    import guide as guided
except ImportError:  # tests import backend.main
    from backend import classifier
    from backend import guide as guided

app = FastAPI()

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
INTERFACE_DIR = PROJECT_DIR / "interface"
SENSOR_DIR = PROJECT_DIR / "bme-code"
# local tests replace the sensor program
SENSOR_BINARY = Path(os.environ.get("BME_SENSOR_BINARY", SENSOR_DIR / "main"))
SENSOR_COMMAND = str(SENSOR_BINARY) if "BME_SENSOR_BINARY" in os.environ else "./main"
MEASUREMENTS_DIR = Path(
    os.environ.get("BME_MEASUREMENTS_DIR", SENSOR_DIR / "measurements")
)

# saved config survives reboot
CONFIG_PATH = Path(os.environ.get("BME_CONFIG_PATH", "/var/lib/bme688/config.json"))
# models and clean air live next to the config
STATE_DIR = Path(os.environ.get("BME_STATE_DIR", CONFIG_PATH.parent))
# the running measurement, kept over a reboot to mark its recording as interrupted
SESSION_PATH = Path(os.environ.get("BME_SESSION_PATH", STATE_DIR / "session.json"))
# test or collect, and how many cycles, the same on every device
GUIDE_SETTINGS_PATH = STATE_DIR / "guide_settings.json"

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

sensor_process = None
sensor_file_started_at = None
sensor_file_duration_minutes = None
sensor_measurement_title = None
# how the last measurement ended when nobody pressed stop, shown once on every page
last_failure = None
sensor_stopping = False
sensor_lock = threading.RLock()

LOG_PATH = Path("/tmp/bme_log.txt")
# newest reading of each sensor, written by the sensor program
LIVE_PATH = Path("/tmp/bme_latest.json")
# exit codes of the sensor program, see bme-code/main.c
FAILURES = {3: "board", 4: "bus", 5: "files"}
START_WAIT_S = 5
FILE_DURATION_OPTIONS = {1, 2, 3, 4, 5, 10, 15, 20, 25, 30}
DEFAULT_FILE_DURATION_MINUTES = 30

class TimeSync(BaseModel):
    timestamp_ms: int


class FileDurationRequest(BaseModel):
    file_duration_minutes: Literal[1, 2, 3, 4, 5, 10, 15, 20, 25, 30] = DEFAULT_FILE_DURATION_MINUTES


class Cycle(BaseModel):
    object: str
    sample_class: Literal["clean_air", "spicy", "bad_smell"]
    distance_cm: float
    notes: str = ""


class StartSensorRequest(BaseModel):
    measurement_title: str
    file_duration_minutes: Optional[Literal[1, 2, 3, 4, 5, 10, 15, 20, 25, 30]] = None
    mode: Optional[Literal["collect", "test"]] = None
    cycles: Optional[list[Cycle]] = None


class GuideTap(BaseModel):
    index: int = 0


class GuideSettings(BaseModel):
    mode: Literal["collect", "test"] = "collect"
    cycles: int = 2


class DownloadFilesRequest(BaseModel):
    paths: list[str]
    archive_name: str = "measurements"


class DeleteFilesRequest(BaseModel):
    paths: list[str]


class ActiveModelRequest(BaseModel):
    id: Optional[str] = None


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


def safe_measurement_title(title: str) -> str:
    title = unicodedata.normalize("NFKC", title.strip())
    if not title or len(title) > 64:
        raise ValueError("Enter a measurement title between 1 and 64 characters")
    safe_title = re.sub(r"[^\w-]+", "-", title, flags=re.UNICODE).strip("-_")
    if not safe_title:
        raise ValueError("The measurement title needs at least one letter or number")
    return safe_title


def get_measurement_folder(title: str):
    safe_title = safe_measurement_title(title)
    title_dir = MEASUREMENTS_DIR / safe_title
    title_dir.mkdir(parents=True, exist_ok=True)
    return safe_title, title_dir


def last_fatal_line() -> str:
    try:
        lines = LOG_PATH.read_text(errors="replace").splitlines()
    except OSError:
        return ""
    fatal = [line for line in lines if line.startswith("[FATAL]")]
    return fatal[-1].replace("[FATAL]", "").strip() if fatal else ""


# the sensor program ended on its own: keep why, mark the recording as interrupted
def record_failure(exit_code: int) -> None:
    global sensor_process, last_failure
    last_failure = {"id": int(time.time() * 1000), "code": FAILURES.get(exit_code, "crashed"),
                    "message": last_fatal_line(), "exit_code": exit_code}
    sensor_process = None
    try:
        with open(LOG_PATH, "a") as log:
            log.write(f"[STOP] The sensor program ended on its own (exit code {exit_code}).\n")
    except OSError:
        pass
    predictor._recover()


# waits until the sensors run, returns the exit code if the program ended while starting
def wait_for_start(process) -> int | None:
    deadline = time.monotonic() + START_WAIT_S
    while time.monotonic() < deadline:
        code = process.poll()
        if code is not None:
            return code
        try:
            log = LOG_PATH.read_text(errors="replace")
        except OSError:
            log = ""
        if "[START]" in log or "[OK] Sensor replay" in log:
            return None
        time.sleep(0.1)
    return None


def sensor_is_running() -> bool:
    with sensor_lock:
        if sensor_process is not None:
            code = sensor_process.poll()
            if code is None:
                return True
            if not sensor_stopping:
                record_failure(code)
                return False
    return subprocess.run(
        ["pgrep", "-x", "main"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0


def require_sensor_stopped() -> None:
    if sensor_is_running():
        raise HTTPException(
            status_code=409,
            detail="Stop measurement before downloading files",
        )


def run_details(folder_name: str, modified_at: float) -> tuple[str, int | None]:
    match = re.match(
        r"^(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})_(\d+)min(?:_\d+)?$",
        folder_name,
    )
    if not match:
        return datetime.fromtimestamp(modified_at).isoformat(timespec="seconds"), None

    recorded_at = datetime.strptime(
        f"{match.group(1)}_{match.group(2)}",
        "%Y-%m-%d_%H-%M-%S",
    ).isoformat(timespec="seconds")
    return recorded_at, int(match.group(3))


def list_measurement_files() -> list[dict]:
    if not MEASUREMENTS_DIR.exists():
        return []

    measurements = []
    for title_dir in MEASUREMENTS_DIR.iterdir():
        if not title_dir.is_dir() or title_dir.is_symlink() or title_dir.name.startswith("."):
            continue

        runs = []
        for run_dir in title_dir.iterdir():
            if not run_dir.is_dir() or run_dir.is_symlink() or run_dir.name.startswith("."):
                continue

            files = []
            for file_path in run_dir.iterdir():
                if not file_path.is_file() or file_path.is_symlink() or file_path.name.startswith("."):
                    continue
                stat = file_path.stat()
                files.append({
                    "name": file_path.name,
                    "path": file_path.relative_to(MEASUREMENTS_DIR).as_posix(),
                    "size_bytes": stat.st_size,
                })
            if not files:
                continue

            files.sort(key=lambda item: item["name"].lower())
            recorded_at, duration_minutes = run_details(run_dir.name, run_dir.stat().st_mtime)
            runs.append({
                "name": run_dir.name,
                "recorded_at": recorded_at,
                "duration_minutes": duration_minutes,
                "size_bytes": sum(item["size_bytes"] for item in files),
                "files": files,
            })

        if not runs:
            continue
        runs.sort(key=lambda item: item["recorded_at"], reverse=True)
        newest = max(item["recorded_at"] for item in runs)
        measurements.append({
            "name": title_dir.name,
            "display_name": title_dir.name.replace("_", " ").replace("-", " "),
            "size_bytes": sum(item["size_bytes"] for item in runs),
            "newest": newest,
            "runs": runs,
        })

    measurements.sort(key=lambda item: item["newest"], reverse=True)
    for measurement in measurements:
        measurement.pop("newest")
    return measurements


def resolve_measurement_file(relative_path: str) -> Path:
    requested = Path(relative_path)
    if requested.is_absolute() or ".." in requested.parts:
        raise HTTPException(status_code=400, detail="Invalid measurement path")
    try:
        root = MEASUREMENTS_DIR.resolve(strict=True)
        file_path = (root / requested).resolve(strict=True)
    except (FileNotFoundError, OSError):
        raise HTTPException(status_code=404, detail="Measurement file not found")
    if not file_path.is_relative_to(root) or not file_path.is_file():
        raise HTTPException(status_code=400, detail="Invalid measurement path")
    return file_path


def safe_archive_name(name: str) -> str:
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "-", name.strip()).strip("-_")
    return safe_name[:64] or "measurements"


def selected_measurement_files(paths: list[str]) -> list[Path]:
    if not paths:
        raise HTTPException(status_code=400, detail="Select at least one file")
    if len(paths) > 5000:
        raise HTTPException(status_code=400, detail="Too many files selected")

    files = []
    seen = set()
    for path in paths:
        file_path = resolve_measurement_file(path)
        if file_path not in seen:
            files.append(file_path)
            seen.add(file_path)
    return files

@app.post("/api/sync-time")
def sync_time(data: TimeSync):
    # a clock change would break the measurement time stamps
    if sensor_is_running():
        return {"status": "skipped", "message": "Clock not changed during a measurement"}
    if os.environ.get("BME_SKIP_CLOCK_SYNC") == "1":  # local tests keep the computer clock
        return {"status": "skipped", "message": "Clock sync disabled"}
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
def save_file_duration(request: FileDurationRequest):
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
    global sensor_measurement_title
    # pgrep also finds a measurement that survived a backend restart
    is_running = sensor_is_running()
    if not is_running:
        sensor_file_started_at = None
        sensor_file_duration_minutes = None
        sensor_measurement_title = None
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
        "measurement_title": sensor_measurement_title,
        "failure": last_failure,
    }


@app.get("/api/files")
def get_measurement_files():
    require_sensor_stopped()
    measurements = list_measurement_files()
    return {
        "status": "success",
        "measurements": measurements,
        "recording_count": sum(len(item["runs"]) for item in measurements),
        "file_count": sum(
            len(run["files"])
            for item in measurements
            for run in item["runs"]
        ),
    }


@app.post("/api/files/download")
def download_measurement_files(request: DownloadFilesRequest):
    require_sensor_stopped()
    files = selected_measurement_files(request.paths)

    if len(files) == 1:
        return FileResponse(files[0], filename=files[0].name)

    archive = tempfile.NamedTemporaryFile(prefix="bme688-", suffix=".zip", delete=False)
    archive_path = Path(archive.name)
    archive.close()
    try:
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for file_path in files:
                zip_file.write(
                    file_path,
                    file_path.relative_to(MEASUREMENTS_DIR.resolve()).as_posix(),
                )
    except Exception:
        archive_path.unlink(missing_ok=True)
        raise

    return FileResponse(
        archive_path,
        media_type="application/zip",
        filename=f"{safe_archive_name(request.archive_name)}.zip",
        background=BackgroundTask(archive_path.unlink, missing_ok=True),
    )


@app.post("/api/files/delete")
def delete_measurement_files(request: DeleteFilesRequest):
    require_sensor_stopped()
    files = selected_measurement_files(request.paths)
    root = MEASUREMENTS_DIR.resolve()
    directories = set()

    for file_path in files:
        directories.add(file_path.parent)
        file_path.unlink()

    for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
        current = directory
        while current != root and current.is_relative_to(root):
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent

    return {
        "status": "success",
        "deleted_count": len(files),
    }

@app.post("/api/start-sensor")
def start_sensor(request: StartSensorRequest):
    global sensor_process, sensor_file_started_at, sensor_file_duration_minutes
    global sensor_measurement_title
    global last_failure
    try:
        if sensor_process and sensor_process.poll() is None:
            return {"status": "error", "message": "Sensor is already running"}

        file_duration_minutes = (
            request.file_duration_minutes
            if request.file_duration_minutes is not None
            else read_file_duration()
        )
        write_file_duration(file_duration_minutes)
        measurement_title, measurement_dir = get_measurement_folder(
            request.measurement_title
        )
            
        with open(LOG_PATH, "w") as f:
            f.write("Starting sensor...\n")
            
        with open(LOG_PATH, "a") as log_file:
            sensor_env = os.environ.copy()
            sensor_env["BME_MEASUREMENT_DIR"] = str(measurement_dir)
            sensor_process = subprocess.Popen(
                [SENSOR_COMMAND, str(file_duration_minutes)],
                cwd=SENSOR_DIR,
                env=sensor_env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )

        # a board, bus or storage problem ends the program right away: no recording, a card instead
        last_failure = None
        exit_code = wait_for_start(sensor_process)
        if exit_code is not None:
            sensor_process = None
            return {"status": "error", "error": FAILURES.get(exit_code, "crashed"),
                    "message": last_fatal_line() or "The sensor program stopped while starting."}

        sensor_file_started_at = time.monotonic()
        sensor_file_duration_minutes = file_duration_minutes
        session = {"title_dir": str(measurement_dir), "started_at": time.time()}
        if request.mode == "test":
            session["guide"] = guided.new_guide("test", [])
        elif request.mode == "collect" and request.cycles:
            session["guide"] = guided.new_guide("collect", [cycle.dict() for cycle in request.cycles])
        classifier.write_json_atomic(SESSION_PATH, session)
        predictor.guide_finished = False
        sensor_measurement_title = measurement_title
        return {
            "status": "success",
            "pid": sensor_process.pid,
            "file_duration_minutes": file_duration_minutes,
            "measurement_title": measurement_title,
            "measurement_path": (str(measurement_dir.relative_to(SENSOR_DIR))
                                 if measurement_dir.is_relative_to(SENSOR_DIR) else str(measurement_dir)),
        }
    except Exception as e:
        with open(LOG_PATH, "a") as f:
            f.write(f"Failed to start: {str(e)}\n")
        return {"status": "error", "message": str(e)}

@app.post("/api/stop-sensor")
def stop_sensor():
    global sensor_process, sensor_file_started_at, sensor_file_duration_minutes
    global sensor_measurement_title, sensor_stopping
    try:
        with sensor_lock:
            sensor_stopping = True
        if sensor_process and sensor_process.poll() is None:
            sensor_process.terminate()
            sensor_process.wait(timeout=3)
            sensor_process = None
        else:
            sensor_process = None
            subprocess.run(["pkill", "-x", "main"])
        SESSION_PATH.unlink(missing_ok=True)
        # everything written so far onto the sd card, before the page says stopped
        os.sync()
        sensor_file_started_at = None
        sensor_file_duration_minutes = None
        sensor_measurement_title = None
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        with sensor_lock:
            sensor_stopping = False

@app.get("/api/latest")
def get_latest():
    try:
        if sensor_process and sensor_process.poll() is not None:
            return {"status": "error", "message": "Sensor process crashed."}
        if not LIVE_PATH.exists():
            return {"status": "error", "message": "Waiting for sensor data..."}
        return {"status": "success", "data": json.loads(LIVE_PATH.read_text())}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/logs")
def get_logs():
    try:
        if not LOG_PATH.exists():
            return {"status": "success", "logs": "No logs yet."}
        
        with open(LOG_PATH, "r") as f:
            lines = f.readlines()
        tail = "".join(lines[-20:])
        return {"status": "success", "logs": tail}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# classifier

def read_config_or_none():
    try:
        return read_config()
    except Exception:
        return None


predictor = classifier.Predictor(
    STATE_DIR, MEASUREMENTS_DIR, SESSION_PATH, sensor_is_running, read_config_or_none,
    on_guide_done=lambda: stop_sensor()
)


@app.on_event("startup")
def start_predictor():
    predictor.start()


def api_error(message: str, status_code: int = 400):
    return JSONResponse({"status": "error", "message": message}, status_code=status_code)


def models_locked():
    if sensor_is_running():
        return api_error("Stop the measurement before changing models.", 409)
    return None


@app.get("/api/models")
def list_models():
    models = predictor.store.models()
    return {
        "status": "success",
        "models": [classifier.summary(model) for model in models],
        "active_id": predictor.store.active_id(),
    }


@app.post("/api/models")
async def upload_model(request: Request, filename: str = "model.json"):
    locked = models_locked()
    if locked:
        return locked
    body = await request.body()
    if len(body) > classifier.MAX_MODEL_BYTES:
        return api_error("The model file must be 5 MiB or smaller.", 413)
    try:
        payload = json.loads(body)
    except ValueError:
        return api_error("This file is not valid JSON.")
    try:
        model = predictor.store.add(payload, filename, len(body))
    except classifier.ModelError as error:
        return api_error(str(error))
    if not predictor.store.active_id():
        predictor.store.set_active(model["id"])
    return {"status": "success", "model": classifier.summary(model), "active_id": predictor.store.active_id()}


@app.post("/api/models/active")
def select_model(request: ActiveModelRequest):
    locked = models_locked()
    if locked:
        return locked
    try:
        predictor.store.set_active(request.id)
    except classifier.ModelError as error:
        return api_error(str(error), 404)
    return {"status": "success", "active_id": predictor.store.active_id()}


@app.delete("/api/models/{model_id}")
def delete_model(model_id: str):
    locked = models_locked()
    if locked:
        return locked
    if not predictor.store.remove(model_id):
        return api_error("Model not found.", 404)
    return {"status": "success", "active_id": predictor.store.active_id()}


@app.get("/api/prediction")
def get_prediction():
    return predictor.snapshot()


# guided cycles
@app.get("/api/guide")
def get_guide():
    return predictor.guide_state()


@app.get("/api/guide-settings")
def get_guide_settings():
    saved = classifier.read_json(GUIDE_SETTINGS_PATH, {}) or {}
    return {"status": "success", **GuideSettings(**saved).dict()}


@app.post("/api/guide-settings")
def save_guide_settings(settings: GuideSettings):
    if not 1 <= settings.cycles <= 6:
        return api_error("Choose between 1 and 6 cycles.")
    classifier.write_json_atomic(GUIDE_SETTINGS_PATH, settings.dict())
    return {"status": "success", **settings.dict()}


@app.post("/api/guide/{action}")
def guide_tap(action: Literal["placed", "removed", "reclean"], tap: GuideTap):
    problem = predictor.guide_record(action, tap.index)
    if problem:
        return api_error(problem, 409)
    return predictor.guide_state()


app.mount("/static", StaticFiles(directory=INTERFACE_DIR, html=True), name="static")

@app.get("/generate_204")
@app.get("/hotspot-detect.html")
@app.get("/")
def captive_portal():
    return RedirectResponse(url="/static/index.html")
