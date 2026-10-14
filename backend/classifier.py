# features must match the notebook, see tests/test_classifier_parity.py
import json
import math
import os
import re
import tempfile
import threading
import time
import hashlib
from datetime import datetime
from pathlib import Path

MAX_MODEL_BYTES = 5 * 1024 * 1024
SUPPORTED_TYPES = {"multinomial_logistic_regression"}
FEATURE_RE = re.compile(r"^(?P<profile>.+)_shape_s(?P<step>\d)$")
SENSOR_FILE_RE = re.compile(r"^sensor(\d)_.*\.csv$")
RUN_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})_(\d+)min(?:_\d+)?$")

DEFAULT_CLIP_OHM = 1.02e8      # sensor maximum, carries no information
TICK_S = 0.14                  # heater time base in parallel mode
KEEP_MS = 20 * 60 * 1000       # cycles kept per sensor
LOOP_SECONDS = 3

# limits from the training data
UNUSUAL_Z = 5.0                # no training window went past 4
UNUSUAL_COUNT = 3
MAX_MISSING_FRACTION = 0.25
MIN_CYCLE_FRACTION = 0.5

# clean air
CALIBRATION_SECONDS = 300
CALIBRATION_MIN_CYCLES = 3
CALIBRATION_STABLE = 0.15      # clean air after warm-up stays below 0.1
REFERENCE_FRESH_HOURS = 12


class ModelError(ValueError):
    pass


# helpers

def write_json_atomic(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def read_json(path: Path, default=None):
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def iso(ms) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000).isoformat(timespec="seconds")


def profile_signature(profile: dict) -> list:
    sleep = profile.get("sleep", profile.get("sleep_sec", 0))
    return [profile.get("mode"), int(profile.get("duty") or 0), int(sleep or 0),
            [int(v) for v in profile.get("temps", [])], [int(v) for v in profile.get("ticks", [])]]


def cycle_seconds(profile: dict) -> float:
    return sum(int(v) for v in profile.get("ticks", [])) * TICK_S


def nice_class(name: str) -> str:
    text = str(name).replace("_", " ").strip()
    return text[:1].upper() + text[1:]


# model validation

def _text(value, name, required=True, maximum=200):
    if value is None and not required:
        return ""
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ModelError(f"{name} must be text between 1 and {maximum} characters.")
    return value.strip()


def _number(value, name, low, high, default=None):
    if value is None and default is not None:
        return default
    if not finite(value) or value < low or value > high:
        raise ModelError(f"{name} must be a number between {low} and {high}.")
    return value


def _numbers(value, length, name):
    if not isinstance(value, list) or len(value) != length or not all(finite(v) for v in value):
        raise ModelError(f"{name} must be a list of {length} numbers.")
    return [float(v) for v in value]


def _requirement(item):
    if not isinstance(item, dict):
        raise ModelError("Each profile requirement must be an object.")
    profile = _text(item.get("profile"), "Profile name")
    count = item.get("count")
    mode = item.get("mode")
    duty = item.get("duty")
    sleep = item.get("sleep", item.get("sleep_sec"))
    temps, ticks = item.get("temps"), item.get("ticks")
    if not isinstance(count, int) or isinstance(count, bool) or count < 1 or count > 8:
        raise ModelError(f"{profile}: sensor count must be 1 to 8.")
    if mode not in ("parallel", "sequential"):
        raise ModelError(f"{profile}: mode must be parallel or sequential.")
    for label, value in (("duty", duty), ("sleep", sleep)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ModelError(f"{profile}: {label} must be a whole number of 0 or more.")
    max_ticks = 28 if mode == "sequential" else 429
    for label, values, low, high in (("temps", temps, 0, 400), ("ticks", ticks, 1, max_ticks)):
        if (not isinstance(values, list) or len(values) != 10 or
                not all(isinstance(v, int) and not isinstance(v, bool) and low <= v <= high for v in values)):
            raise ModelError(f"{profile}: {label} must be 10 whole numbers from {low} to {high}.")
    return {"profile": profile, "count": count, "mode": mode, "duty": duty, "sleep": sleep,
            "temps": list(temps), "ticks": list(ticks)}


def validate_model(payload) -> dict:
    if not isinstance(payload, dict):
        raise ModelError("The file must contain a JSON object.")
    meta, model = payload.get("metadata"), payload.get("model")
    if not isinstance(meta, dict):
        raise ModelError("The file has no metadata section.")
    if not isinstance(model, dict):
        raise ModelError("The file has no model section with weights. Export the model from the notebook.")

    name = _text(meta.get("name"), "Model name")
    version = _text(str(meta["version"]) if finite(meta.get("version")) else meta.get("version", "1.0"), "Version", maximum=40)
    description = _text(meta.get("description"), "Description", required=False, maximum=500)
    classes = meta.get("classes")
    if (not isinstance(classes, list) or not 2 <= len(classes) <= 20 or
            not all(isinstance(c, str) and c.strip() for c in classes) or len(set(classes)) != len(classes)):
        raise ModelError("Classes must be a list of 2 to 20 different names.")
    window = _number(meta.get("window_seconds"), "Analysis window", 10, 1800)
    warmup = _number(meta.get("warmup_seconds"), "Warm-up", 0, 7200, default=900)
    interval = _number(meta.get("prediction_interval_seconds"), "Prediction interval", 5, 600, default=30)

    requirements = meta.get("profile_requirements")
    if not isinstance(requirements, list) or not 1 <= len(requirements) <= 8:
        raise ModelError("Add profile_requirements: the heater profiles and sensor counts the model was trained with.")
    requirements = [_requirement(item) for item in requirements]
    if sum(r["count"] for r in requirements) > 8:
        raise ModelError("The profile requirements use more than 8 sensors.")
    names = [r["profile"] for r in requirements]
    if len(set(names)) != len(names):
        raise ModelError("Each heater profile may appear only once in profile_requirements.")
    signatures = [json.dumps(profile_signature(r)) for r in requirements]
    if len(set(signatures)) != len(signatures):
        raise ModelError("Two profile requirements have identical settings.")

    if model.get("type") not in SUPPORTED_TYPES:
        raise ModelError(f"Model type '{model.get('type')}' is not supported. Use multinomial logistic regression.")
    features = model.get("features")
    if not isinstance(features, list) or not features or len(set(features)) != len(features):
        raise ModelError("The model needs a list of different feature names.")
    steps_by_profile = {}
    for feature in features:
        match = FEATURE_RE.match(feature) if isinstance(feature, str) else None
        if not match:
            raise ModelError(f"Feature '{feature}' cannot be computed on the device (only curve-shape features are supported).")
        profile, step = match.group("profile"), int(match.group("step"))
        if profile not in names:
            raise ModelError(f"Feature '{feature}' uses profile {profile}, which is not in profile_requirements.")
        steps_by_profile.setdefault(profile, []).append(step)
    use_steps = model.get("use_steps")
    if use_steps is not None:
        expected = {f"{p}_shape_s{i}" for p, steps in use_steps.items() for i in steps} if isinstance(use_steps, dict) else None
        if expected != set(features):
            raise ModelError("use_steps does not match the feature list.")
    count = len(features)
    impute = _numbers(model.get("impute_median"), count, "impute_median")
    mean = _numbers(model.get("scaler_mean"), count, "scaler_mean")
    scale = _numbers(model.get("scaler_scale"), count, "scaler_scale")
    if any(v <= 0 for v in scale):
        raise ModelError("scaler_scale values must be greater than zero.")
    # scikit-learn sorts the classes, so coef follows model.classes
    model_classes = model.get("classes", classes)
    if not isinstance(model_classes, list) or sorted(model_classes) != sorted(classes):
        raise ModelError("The classes in metadata and model are different.")
    display_classes, classes = list(classes), list(model_classes)
    coef, intercept = model.get("coef"), model.get("intercept")
    rows = len(classes) if len(classes) > 2 else None
    if not isinstance(coef, list) or (len(coef) not in ((rows,) if rows else (1, 2))):
        raise ModelError(f"coef must have one row per class ({len(classes)} rows).")
    coef = [_numbers(row, count, "Each coef row") for row in coef]
    intercept = _numbers(intercept, len(coef), "intercept")
    unknown_below = _number(model.get("unknown_below"), "unknown_below", 0, 1, default=0.6)
    clip = _number(model.get("clip_ohm"), "clip_ohm", 1, 1e12, default=DEFAULT_CLIP_OHM)

    training = payload.get("training") if isinstance(payload.get("training"), dict) else {}
    accuracy = {key: training.get(f"balanced_accuracy_{key}") for key in ("unseen_board", "unseen_session")
                if finite(training.get(f"balanced_accuracy_{key}"))}
    return {
        "name": name, "version": version, "description": description, "classes": list(classes),
        "display_classes": display_classes,
        "window_seconds": window, "warmup_seconds": warmup, "prediction_interval_seconds": interval,
        "requirements": requirements, "features": list(features), "steps_by_profile": steps_by_profile,
        "impute": impute, "mean": mean, "scale": scale, "coef": coef, "intercept": intercept,
        "unknown_below": unknown_below, "clip_ohm": clip,
        "accuracy": accuracy, "sessions": training.get("sessions") if finite(training.get("sessions")) else None,
    }


# model storage

class ModelStore:
    def __init__(self, state_dir: Path):
        self.dir = state_dir / "models"
        self.active_path = state_dir / "active_model.json"
        self._cache = {}

    def _load(self, path: Path):
        stamp = path.stat().st_mtime_ns
        cached = self._cache.get(path.name)
        if cached and cached[0] == stamp:
            return cached[1]
        stored = read_json(path)
        if not isinstance(stored, dict) or "payload" not in stored:
            return None
        try:
            model = validate_model(stored["payload"])
        except ModelError:
            return None
        model.update(id=path.stem, filename=stored.get("filename") or path.name,
                     uploaded_at=stored.get("uploaded_at"), size_bytes=stored.get("size_bytes") or path.stat().st_size)
        self._cache[path.name] = (stamp, model)
        return model

    def models(self) -> list:
        if not self.dir.exists():
            return []
        models = [m for m in (self._load(p) for p in sorted(self.dir.glob("*.json"))) if m]
        return sorted(models, key=lambda m: m.get("uploaded_at") or "")

    def get(self, model_id):
        if not model_id or not re.fullmatch(r"[a-z0-9-]{1,80}", model_id):
            return None
        path = self.dir / f"{model_id}.json"
        return self._load(path) if path.exists() else None

    def active_id(self):
        value = (read_json(self.active_path, {}) or {}).get("id")
        return value if self.get(value) else None

    def active(self):
        return self.get(self.active_id())

    def add(self, payload: dict, filename: str, size_bytes: int) -> dict:
        model = validate_model(payload)
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:8]
        slug = re.sub(r"[^a-z0-9]+", "-", f"{model['name']}-v{model['version']}".lower()).strip("-")[:60] or "model"
        model_id = f"{slug}-{digest}"
        path = self.dir / f"{model_id}.json"
        if path.exists():
            raise ModelError("This model is already on the device.")
        safe_name = re.sub(r"[^\w.-]+", "_", Path(filename or "model.json").name)[:120]
        write_json_atomic(path, {"filename": safe_name, "size_bytes": size_bytes,
                                 "uploaded_at": datetime.now().isoformat(timespec="seconds"), "payload": payload})
        return self.get(model_id)

    def remove(self, model_id) -> bool:
        model = self.get(model_id)
        if not model:
            return False
        if self.active_id() == model_id:
            self.set_active(None)
        (self.dir / f"{model_id}.json").unlink(missing_ok=True)
        self._cache.pop(f"{model_id}.json", None)
        return True

    def set_active(self, model_id) -> None:
        if model_id is not None and not self.get(model_id):
            raise ModelError("Model not found.")
        write_json_atomic(self.active_path, {"id": model_id})


def summary(model: dict) -> dict:
    sensors, sensor_profiles = [], {}
    for req in model["requirements"]:
        for _ in range(req["count"]):
            sensors.append(len(sensors) + 1)
            sensor_profiles[len(sensors)] = {key: req[key] for key in ("mode", "duty", "sleep", "temps", "ticks")}
            sensor_profiles[len(sensors)]["profile"] = req["profile"]
    return {
        "id": model["id"], "name": model["name"], "version": model["version"],
        "description": model["description"], "classes": model["display_classes"],
        "class_labels": [nice_class(c) for c in model["display_classes"]],
        "profiles": [r["profile"] for r in model["requirements"]],
        "sensors": sensors, "sensor_profiles": sensor_profiles,
        "window_seconds": model["window_seconds"], "warmup_seconds": model["warmup_seconds"],
        "prediction_interval_seconds": model["prediction_interval_seconds"],
        "feature_count": len(model["features"]), "unknown_below": model["unknown_below"],
        "accuracy": model["accuracy"], "sessions": model["sessions"],
        "size_bytes": model["size_bytes"], "filename": model["filename"], "uploaded_at": model["uploaded_at"],
    }


# sensor setup

def sensor_setup(config) -> dict:
    setup = {}
    if not isinstance(config, dict):
        return setup
    presets = config.get("presets") or []
    assignments = config.get("assignments") or {}
    for sensor in range(1, 9):
        assigned = assignments.get(str(sensor), assignments.get(sensor)) if isinstance(assignments, dict) else None
        matches = [p for p in presets if isinstance(p, dict) and p.get("id") == assigned]
        if len(matches) == 1:
            preset = matches[0]
            try:
                setup[sensor] = {"name": preset.get("name") or "Assigned profile",
                                 "signature": profile_signature(preset), "cycle_s": cycle_seconds(preset)}
            except (TypeError, ValueError):
                continue
    return setup


def match_sensors(model: dict, setup: dict):
    # same matching rule as the interface
    used, mapping, issues = set(), {}, []
    for req in model["requirements"]:
        signature = profile_signature(req)
        candidates = [s for s in sorted(setup) if setup[s]["signature"] == signature and s not in used]
        if len(candidates) < req["count"]:
            issues.append(f"{req['profile']}: needs {req['count']} sensors, found {len(candidates)} with matching settings.")
        chosen = candidates[:req["count"]]
        used.update(chosen)
        mapping[req["profile"]] = chosen
    return mapping, issues


# measurement files

# reads a growing csv and keeps complete heater cycles
class CycleReader:

    def __init__(self, path: Path, clip_ohm: float):
        self.path, self.clip = path, clip_ohm
        self.offset, self.rest = 0, b""
        self.current, self.t0 = {}, None
        self.cycles, self.first_t, self.last_t = [], None, None

    def update(self) -> None:
        try:
            size = self.path.stat().st_size
            if size < self.offset:
                self.__init__(self.path, self.clip)
            if size == self.offset:
                return
            with self.path.open("rb") as handle:
                handle.seek(self.offset)
                chunk = handle.read(size - self.offset)
        except OSError:
            return
        self.offset += len(chunk)
        lines = (self.rest + chunk).split(b"\n")
        self.rest = lines.pop()
        for raw in lines:
            parts = raw.replace(b"\x00", b"").decode("latin1").strip().split(",")
            if len(parts) != 8:
                continue
            try:
                t, step, ohm = int(parts[0]), int(parts[2]), float(parts[4])
            except ValueError:
                continue
            if not 0 <= step <= 9:
                continue
            if self.first_t is None:
                self.first_t = t
            self.last_t = t
            if step == 0:
                if len(self.current) == 10:
                    self.cycles.append((self.t0, [self.current[i] for i in range(10)]))
                self.current, self.t0 = {}, t
            self.current[step] = math.log(ohm) if 0 < ohm < self.clip else None
        if self.cycles and self.last_t is not None:
            keep_from = self.last_t - KEEP_MS
            if self.cycles[0][0] < keep_from:
                self.cycles = [c for c in self.cycles if c[0] >= keep_from]

    def complete_cycles(self) -> list:
        if len(self.current) == 10 and self.t0 is not None:
            return self.cycles + [(self.t0, [self.current[i] for i in range(10)])]
        return self.cycles


# one measurement can span several run folders
class LiveData:

    def __init__(self, measurements_dir: Path, session_path: Path, clip_ohm=DEFAULT_CLIP_OHM):
        self.root, self.session_path, self.clip = measurements_dir, session_path, clip_ohm
        self.readers, self.key = {}, None
        self.runs, self.first_t = [], None
        self.retired = set()   # older files, already read

    def _session_runs(self):
        session = read_json(self.session_path, {}) or {}
        title_dir = Path(session["title_dir"]) if session.get("title_dir") else None
        started = session.get("started_at")
        if title_dir is None:
            # no session file, use the newest run folder
            candidates = [r for r in self.root.glob("*/*") if r.is_dir() and RUN_RE.match(r.name)] if self.root.exists() else []
            if not candidates:
                return None, []
            title_dir = max(candidates, key=lambda r: r.name).parent
        if not title_dir.exists():
            return title_dir, []
        runs = []
        for run in title_dir.iterdir():
            match = RUN_RE.match(run.name)
            if not run.is_dir() or not match:
                continue
            start = datetime.strptime(match.group(1), "%Y-%m-%d_%H-%M-%S").timestamp()
            runs.append((start, int(match.group(2)), run))
        runs.sort()
        if started is not None:
            runs = [r for r in runs if r[0] >= started - 5]
        else:
            chain = runs[-1:]
            for run in reversed(runs[:-1]):
                if chain[0][0] - run[0] <= run[1] * 60 + 120:
                    chain.insert(0, run)
                else:
                    break
            runs = chain
        return title_dir, [r[2] for r in runs]

    def update(self) -> None:
        title_dir, runs = self._session_runs()
        key = (str(title_dir), str(runs[0]) if runs else None)
        if key != self.key:
            self.readers, self.key, self.first_t, self.retired = {}, key, None, set()
        self.runs = runs
        for index, run in enumerate(runs):
            if index < len(runs) - 3:
                continue  # a window only needs the newest folders
            try:
                files = [f for f in run.iterdir() if SENSOR_FILE_RE.match(f.name)]
            except OSError:
                continue
            for csv_path in files:
                if csv_path in self.retired:
                    continue
                reader = self.readers.get(csv_path)
                if reader is None:
                    reader = self.readers[csv_path] = CycleReader(csv_path, self.clip)
                    reader.update()
                elif index >= len(runs) - 2:
                    reader.update()
        if self.first_t is None and runs:
            # measurement start, for the warm-up
            firsts = []
            for csv_path in (f for f in runs[0].iterdir() if SENSOR_FILE_RE.match(f.name)):
                reader = self.readers.get(csv_path)
                if reader is None:
                    reader = CycleReader(csv_path, self.clip)
                    reader.update()
                if reader.first_t:
                    firsts.append(reader.first_t)
            self.first_t = min(firsts) if firsts else None
        keep = set(runs[-3:])
        for csv_path in [p for p in self.readers if p.parent not in keep]:
            self.retired.add(csv_path)
            del self.readers[csv_path]

    def cycles_by_sensor(self) -> dict:
        out = {}
        for path, reader in list(self.readers.items()):
            sensor = int(SENSOR_FILE_RE.match(path.name).group(1))
            out.setdefault(sensor, []).extend(reader.complete_cycles())
        for cycles in out.values():
            cycles.sort(key=lambda c: c[0])
        return out

    def last_t(self):
        values = [r.last_t for r in list(self.readers.values()) if r.last_t]
        return max(values) if values else None

    def newest_run(self):
        return self.runs[-1] if self.runs else None


# features and model

def window_features(model: dict, mapping: dict, cycles: dict, refs: dict, t_end: int) -> tuple:
    # same window rule as the notebook
    low = t_end - model["window_seconds"] * 1000
    values, counts = {}, {}
    for profile, steps in model["steps_by_profile"].items():
        sums, numbers, used_cycles = [0.0] * 10, [0] * 10, 0
        for sensor in mapping.get(profile, []):
            ref = refs.get(sensor)
            if ref is None:
                continue
            for t0, ln_r in cycles.get(sensor, []):
                if not low < t0 <= t_end:
                    continue
                used_cycles += 1
                for i in steps:
                    if ref[i] is not None and ln_r[i] is not None:
                        sums[i] += ref[i] - ln_r[i]
                        numbers[i] += 1
        counts[profile] = used_cycles
        mean = {i: sums[i] / numbers[i] for i in steps if numbers[i]}
        level = sum(mean.values()) / len(mean) if mean else None
        for i in steps:
            values[f"{profile}_shape_s{i}"] = mean[i] - level if i in mean else None
    return values, counts


def classify(model: dict, values: dict) -> dict:
    x, missing = [], 0
    for j, name in enumerate(model["features"]):
        value = values.get(name)
        if value is None:
            missing += 1
            value = model["impute"][j]
        x.append((value - model["mean"][j]) / model["scale"][j])
    logits = [b + sum(w * v for w, v in zip(row, x)) for row, b in zip(model["coef"], model["intercept"])]
    if len(logits) == 1:  # binary models store one row
        p = 1 / (1 + math.exp(-logits[0]))
        probabilities = [1 - p, p]
    else:
        top = max(logits)
        exps = [math.exp(v - top) for v in logits]
        probabilities = [e / sum(exps) for e in exps]
    far = sum(1 for v in x if abs(v) > UNUSUAL_Z)
    return {"probabilities": probabilities, "missing": missing, "unusual": far, "max_z": max(abs(v) for v in x)}


# live predictor

class Predictor:
    def __init__(self, state_dir: Path, measurements_dir: Path, session_path: Path, is_running, read_config):
        self.state_dir = state_dir
        self.store = ModelStore(state_dir)
        self.calibration_path = state_dir / "calibration.json"
        self.data = LiveData(measurements_dir, session_path)
        self.session_path = session_path
        self.is_running, self.read_config = is_running, read_config
        self.lock = threading.Lock()
        self.result = None
        self.result_checks = []        # checks of the last prediction
        self.status_cache = None
        self.recording = None
        self.last_calibration = None   # outcome of the last recording
        self.skip_warmup_key = None
        self.last_prediction_t = None
        self.thread = None
        self.stop_event = threading.Event()

    def calibration(self):
        return read_json(self.calibration_path)

    # background loop
    def start(self):
        if self.thread:
            return
        self.thread = threading.Thread(target=self._loop, name="smell-predictor", daemon=True)
        self.thread.start()

    def _loop(self):
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception as error:  # never let the loop die
                with self.lock:
                    self.status_cache = {"state": "error", "message": f"Classifier error: {error}"}
            self.stop_event.wait(LOOP_SECONDS)

    def session_key(self):
        session = read_json(self.session_path, {}) or {}
        return f"{session.get('title_dir')}|{session.get('started_at')}"

    def tick(self, now_ms=None):
        now_ms = now_ms or int(time.time() * 1000)
        running = self.is_running()
        model = self.store.active()
        if not running:
            with self.lock:
                if self.recording:
                    self.last_calibration = {"ok": False, "at": time.time(),
                                             "message": "The measurement stopped before the clean-air recording finished."}
                self.recording = None
                self.result, self.last_prediction_t, self.result_checks = None, None, []
                self.status_cache = self._status(model, running=False)
            return
        self.data.clip = model["clip_ohm"] if model else DEFAULT_CLIP_OHM
        self.data.update()
        self._finish_recording()
        status = self._status(model, running=True, now_ms=now_ms)
        if status.get("due"):
            self._predict(model, status)
        elif status.get("state") == "ready" and self.result:
            status["checks"].extend(self.result_checks)
            status["state"] = self.result["state"]
            status["message"] = self.result.get("message", "")
        status.pop("due", None)
        status.pop("_ctx", None)
        with self.lock:
            self.status_cache = status

    def snapshot(self):
        with self.lock:
            status = dict(self.status_cache or self._status(self.store.active(), running=self.is_running()))
            status.pop("due", None)
            status.pop("_ctx", None)
            status["result"] = self.result
            status["calibration"] = self._calibration_view()
            return status

    # clean-air reference
    def _resolve_reference(self, model, mapping, setup, now_ms):
        cal = self.calibration()
        warnings = []
        if isinstance(cal, dict) and isinstance(cal.get("sensors"), dict):
            refs, missing = {}, []
            for sensors in mapping.values():
                for sensor in sensors:
                    entry = cal["sensors"].get(str(sensor))
                    if entry and entry.get("signature") == setup.get(sensor, {}).get("signature"):
                        refs[sensor] = entry["ln_r0"]
                    else:
                        missing.append(sensor)
            if all(any(s in refs for s in sensors) for sensors in mapping.values()):
                age_h = (now_ms - cal.get("recorded_ms", now_ms)) / 3600000
                if missing:
                    warnings.append(f"Sensors {', '.join(map(str, missing))} have no clean-air value and are left out.")
                if age_h > REFERENCE_FRESH_HOURS:
                    warnings.append(f"Clean air was recorded {age_h:.0f} hours ago. Record it again for better results.")
                warnings.extend(cal.get("warnings") or [])
                return refs, {"source": "device", "recorded_at": cal.get("recorded_at"),
                              "age_s": max(0, round((now_ms - cal.get("recorded_ms", now_ms)) / 1000)),
                              "label": "Recorded on this device", "warnings": warnings}
        hint = "Record clean air before predicting: the model compares every smell with clean air from these sensors."
        return None, {"source": "none", "label": "No clean-air reference", "warnings": [], "hint": hint}

    # status and checks
    def _status(self, model, running, now_ms=None):
        now_ms = now_ms or int(time.time() * 1000)
        status = {"status": "success", "running": running, "model": None, "checks": [], "warnings": []}
        if not model:
            status.update(state="no_model", message="Select a model on the Classifier page.")
            return status
        status["model"] = {"id": model["id"], "name": model["name"], "version": model["version"],
                           "classes": model["classes"], "class_labels": [nice_class(c) for c in model["classes"]],
                           "window_seconds": model["window_seconds"],
                           "prediction_interval_seconds": model["prediction_interval_seconds"]}
        checks = status["checks"]
        setup = sensor_setup(self.read_config())
        mapping, issues = match_sensors(model, setup)
        checks.append({"id": "profiles", "ok": not issues, "label": "Heater profiles match the model",
                       "detail": " ".join(issues) or None})
        if issues:
            status.update(state="incompatible", message="The heater profiles don't match the model.", issues=issues)
            return status
        refs, reference = self._resolve_reference(model, mapping, setup, now_ms)
        status["reference"] = reference
        if not running:
            checks.append({"id": "reference", "ok": refs is not None, "warn": bool(reference.get("warnings")),
                           "label": "Clean-air reference", "detail": reference.get("hint") or reference["label"]})
            status.update(state="idle", message="Start a measurement to get predictions.")
            return status

        last_t = self.data.last_t()
        first_t = self.data.first_t
        stale_limit = max([v["cycle_s"] for v in setup.values()] + [30]) * 2 * 1000 + 15000
        fresh = last_t is not None and now_ms - last_t < stale_limit
        checks.append({"id": "data", "ok": fresh, "label": "Sensor data arriving",
                       "detail": None if fresh else "No new readings from the sensors."})
        if last_t is None:
            status.update(state="starting", message="Waiting for the first sensor readings.")
            return status
        elapsed = (last_t - first_t) / 1000 if first_t else 0
        warmup = model["warmup_seconds"]
        skipped = self.skip_warmup_key == self.session_key()
        warm = elapsed >= warmup
        status["measurement"] = {"elapsed_s": round(elapsed), "warmup_s": warmup,
                                 "warmup_remaining_s": max(0, round(warmup - elapsed)), "warmup_skipped": skipped and not warm}
        checks.append({"id": "warmup", "ok": warm, "warn": skipped and not warm, "label": "Sensors warmed up",
                       "detail": None if warm else f"{max(0, round((warmup - elapsed) / 60))} min left"
                       + (" (skipped: predictions may be wrong)" if skipped else "")})
        checks.append({"id": "reference", "ok": refs is not None, "warn": bool(reference.get("warnings")),
                       "label": "Clean-air reference", "detail": reference.get("hint") or " ".join(reference["warnings"]) or reference["label"]})
        if not fresh:
            status.update(state="no_data", message="No new readings from the sensors. Check the measurement log.")
            return status
        if self.recording:
            status.update(state="calibrating", message="Recording clean air. Keep smells away from the sensors.")
            return status
        if not warm and not skipped:
            status.update(state="warming", message="Waiting for the sensors to settle before analysing.")
            return status
        if refs is None:
            status.update(state="no_reference", message=reference["hint"])
            return status
        window_ready_at = first_t + (warmup if not skipped else 0) * 1000 + model["window_seconds"] * 1000
        if last_t < window_ready_at:
            status.update(state="collecting", message="Collecting the first analysis window.",
                          collecting_remaining_s=round((window_ready_at - last_t) / 1000))
            return status
        status["warnings"] = []  # reference notes are shown elsewhere
        if skipped and not warm:
            status["warnings"].append("Warm-up was skipped. Predictions during warm-up are often wrong.")
        interval_ms = model["prediction_interval_seconds"] * 1000
        due = self.result is None or self.last_prediction_t is None or last_t - self.last_prediction_t >= interval_ms
        status.update(state="ready", message="", due=due,
                      _ctx={"mapping": mapping, "refs": refs, "setup": setup, "reference": reference})
        return status

    # prediction
    def _predict(self, model, status):
        ctx = status["_ctx"]
        mapping, refs, setup = ctx["mapping"], ctx["refs"], ctx["setup"]
        cycles = self.data.cycles_by_sensor()
        used = [s for sensors in mapping.values() for s in sensors]
        t_end = max((cycles[s][-1][0] for s in used if cycles.get(s)), default=None)
        if t_end is None:
            return
        values, counts = window_features(model, mapping, cycles, refs, t_end)
        window = model["window_seconds"]
        problems, notes = [], []
        for profile, sensors in mapping.items():
            expected = window / max(setup[sensors[0]]["cycle_s"], 1) * len([s for s in sensors if s in refs]) if sensors else 0
            if counts.get(profile, 0) < max(1, math.floor(expected * MIN_CYCLE_FRACTION)):
                problems.append(f"{profile}: {counts.get(profile, 0)} heater cycles in the window, expected about {round(expected)}.")
            for sensor in sensors:
                if sensor in refs and not any(t_end - window * 1000 < c[0] <= t_end for c in cycles.get(sensor, [])):
                    notes.append(f"Sensor {sensor} sent no complete cycles in this window.")
        out = classify(model, values)
        missing_ok = out["missing"] <= MAX_MISSING_FRACTION * len(model["features"])
        status["checks"].append({"id": "window", "ok": not problems and missing_ok, "warn": bool(notes),
                                 "label": "Enough data in the analysis window",
                                 "detail": " ".join(problems + notes) or None})
        status["warnings"].extend(notes)
        probabilities = out["probabilities"]
        order = sorted(range(len(probabilities)), key=lambda i: -probabilities[i])
        top = order[0]
        unusual = out["unusual"] >= UNUSUAL_COUNT
        status["checks"].append({"id": "pattern", "ok": not unusual, "label": "Pattern similar to the training data",
                                 "detail": f"{out['unusual']} of {len(model['features'])} values far outside the training range." if unusual else None})
        if problems or not missing_ok:
            state, label = "insufficient", None
            status["message"] = "Not enough sensor data in the last window to make a prediction."
        elif unusual:
            state, label = "unusual", None
            status["message"] = "This pattern is unlike anything in the training data, so the model can't classify it."
        elif probabilities[top] < model["unknown_below"]:
            state, label = "unknown", None
            status["message"] = "No class is above the confidence threshold."
        else:
            state, label = "ready", model["classes"][top]
        status["state"] = state
        self.result_checks = status["checks"][-2:]
        self.last_prediction_t = t_end
        self.result = {
            "state": state, "label": label, "label_text": nice_class(label) if label else None,
            "top_class": model["classes"][top], "top_text": nice_class(model["classes"][top]),
            "confidence": round(probabilities[top], 4),
            "probabilities": [{"class": model["classes"][i], "label": nice_class(model["classes"][i]),
                               "p": round(probabilities[i], 4)} for i in order],
            "t_ms": t_end, "time": iso(t_end), "window_seconds": window,
            "missing_features": out["missing"], "unusual_features": out["unusual"], "max_z": round(out["max_z"], 2),
            "reference": ctx["reference"]["source"], "warnings": list(status["warnings"]),
            "message": status.get("message", ""),
        }
        self._log(model, self.result)

    def _log(self, model, result):
        run = self.data.newest_run()
        if not run:
            return
        path = run / "predictions.csv"
        classes = model["classes"]
        try:
            new = not path.exists()
            with path.open("a", encoding="utf-8") as handle:
                if new:
                    handle.write("Timestamp_ms,Time,Model,Version,State,Prediction,Confidence,"
                                 + ",".join(f"P_{c}" for c in classes) + ",Reference,Missing_values,Unusual_values,Warnings\n")
                probs = {p["class"]: p["p"] for p in result["probabilities"]}
                warnings = " | ".join(result["warnings"]).replace('"', "'")
                handle.write(f"{result['t_ms']},{result['time']},\"{model['name']}\",{model['version']},{result['state']},"
                             f"{result['label'] or ''},{result['confidence']:.4f},"
                             + ",".join(f"{probs[c]:.4f}" for c in classes)
                             + f",{result['reference']},{result['missing_features']},{result['unusual_features']},\"{warnings}\"\n")
        except OSError:
            pass

    # clean-air recording
    def start_recording(self, duration_s=CALIBRATION_SECONDS, now_ms=None):
        if not self.is_running():
            raise ValueError("Start a measurement first: the sensors only run while measuring.")
        if self.recording:
            raise ValueError("Clean air is already being recorded.")
        if not 60 <= duration_s <= 1800:
            raise ValueError("The recording must last 1 to 30 minutes.")
        now_ms = now_ms or self.data.last_t() or int(time.time() * 1000)
        with self.lock:
            self.recording = {"start_ms": now_ms, "end_ms": now_ms + int(duration_s * 1000),
                              "duration_s": duration_s, "session": self.session_key()}
            self.last_calibration = None
            if self.status_cache:
                self.status_cache["state"] = "calibrating"
        return self._calibration_view()

    def cancel_recording(self):
        with self.lock:
            self.recording = None

    def _calibration_view(self):
        rec = self.recording
        last = self.last_calibration
        if last and time.time() - last.get("at", 0) > 120:  # show the outcome for two minutes
            last = self.last_calibration = None
        view = {"recording": bool(rec), "last": last}
        if rec:
            last_t = self.data.last_t() or rec["start_ms"]
            view.update(duration_s=rec["duration_s"],
                        remaining_s=max(0, round((rec["end_ms"] - max(last_t, rec["start_ms"])) / 1000)))
        cal = self.calibration()
        if isinstance(cal, dict):
            view["saved"] = {"recorded_at": cal.get("recorded_at"),
                             "sensors": sorted(int(s) for s in (cal.get("sensors") or {})),
                             "warnings": cal.get("warnings") or [], "during_warmup": cal.get("during_warmup", False)}
        return view

    def _finish_recording(self):
        rec = self.recording
        if not rec:
            return
        last_t = self.data.last_t()
        if last_t is None or last_t < rec["end_ms"]:
            return
        cycles = self.data.cycles_by_sensor()
        setup = sensor_setup(self.read_config())
        mid = (rec["start_ms"] + rec["end_ms"]) / 2
        sensors, warnings, worst = {}, [], 0.0
        for sensor, items in cycles.items():
            inside = [c for c in items if rec["start_ms"] <= c[0] < rec["end_ms"]]
            if len(inside) < CALIBRATION_MIN_CYCLES or sensor not in setup:
                continue
            ln_r0 = [median([c[1][i] for c in inside]) for i in range(10)]
            first = [median([c[1][i] for c in inside if c[0] < mid]) for i in range(10)]
            second = [median([c[1][i] for c in inside if c[0] >= mid]) for i in range(10)]
            changes = [abs(a - b) for a, b in zip(first, second) if a is not None and b is not None]
            change = median(changes) or 0.0
            worst = max(worst, change)
            sensors[str(sensor)] = {"profile": setup[sensor]["name"], "signature": setup[sensor]["signature"],
                                    "ln_r0": ln_r0, "cycles": len(inside), "change": round(change, 4)}
        if not sensors:
            with self.lock:
                self.recording = None
                self.last_calibration = {"ok": False, "at": time.time(), "message": "Not enough sensor data was recorded. Try again."}
            return
        missing = [s for s in setup if str(s) not in sensors]
        if missing:
            warnings.append(f"Sensors {', '.join(map(str, missing))} sent too little data and have no clean-air value.")
        if worst > CALIBRATION_STABLE:
            warnings.append("The readings changed during the clean-air recording (sensors still warming up, or a smell nearby).")
        first_t = self.data.first_t
        model = self.store.active()
        warmup = model["warmup_seconds"] if model else 900
        during_warmup = bool(first_t and (rec["start_ms"] - first_t) / 1000 < warmup)
        if during_warmup:
            warnings.append("Clean air was recorded during warm-up, so it may not match the warmed-up sensors.")
        payload = {"recorded_at": iso(rec["end_ms"]), "recorded_ms": rec["end_ms"], "duration_s": rec["duration_s"],
                   "sensors": sensors, "warnings": warnings, "during_warmup": during_warmup,
                   "stability": round(worst, 4)}
        write_json_atomic(self.calibration_path, payload)
        with self.lock:
            self.recording = None
            self.last_calibration = {"ok": True, "warnings": warnings, "at": time.time(),
                                     "message": "Clean air saved." + (" Check the notes." if warnings else "")}
            self.result, self.last_prediction_t = None, None

    def skip_warmup(self):
        if not self.is_running():
            raise ValueError("Start a measurement first.")
        self.skip_warmup_key = self.session_key()


def median(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2
