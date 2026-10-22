# features must match the notebook, see tests/test_classifier_parity.py
import json
import math
import os
import re
import socket
import tempfile
import threading
import time
import hashlib
from datetime import datetime
from pathlib import Path

try:
    import guide as guided
except ImportError:
    from backend import guide as guided

MAX_MODEL_BYTES = 5 * 1024 * 1024
SUPPORTED_TYPES = {"multinomial_logistic_regression"}
FEATURE_RE = re.compile(r"^(?P<profile>.+)_shape_s(?P<step>\d)$")
SENSOR_FILE_RE = re.compile(r"^sensor(\d)_.*\.csv$")
REFERENCE_KEY_RE = re.compile(r"^sensor([1-8])_(.+)$")
RUN_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})_(\d+)min(?:_\d+)?$")

DEFAULT_CLIP_OHM = 1.02e8      # sensor maximum, carries no information
TICK_S = 0.14                  # heater time base in parallel mode
KEEP_MS = 20 * 60 * 1000       # cycles kept per sensor
LOOP_SECONDS = 3

# clean air from the measurement: minutes 10-15, the first minutes are still settling
MEASURED_REFERENCE_FROM_S = 600
MEASURED_REFERENCE_TO_S = 900

# limits from the training data
UNUSUAL_Z = 5.0                # no training window went past 4
UNUSUAL_COUNT = 3
MAX_MISSING_FRACTION = 0.25
MIN_CYCLE_FRACTION = 0.5



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

    references = {}
    raw_refs = payload.get("clean_air_reference") or {}
    if not isinstance(raw_refs, dict):
        raise ModelError("clean_air_reference must have one entry per board.")
    for board, entry in raw_refs.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("ln_r0"), dict):
            raise ModelError(f"clean_air_reference for {board} needs ln_r0 values.")
        values = {}
        for key, steps in entry["ln_r0"].items():
            match = REFERENCE_KEY_RE.match(key)
            if (not match or not isinstance(steps, list) or len(steps) != 10 or
                    not all(v is None or finite(v) for v in steps)):
                raise ModelError(f"clean_air_reference {board}/{key} must hold 10 values.")
            values[(int(match.group(1)), match.group(2))] = [None if v is None else float(v) for v in steps]
        references[str(board)] = {"recorded_at": entry.get("recorded_at"), "ln_r0": values}
    if not references:
        raise ModelError("The model has no clean_air_reference. Export it again with the notebook.")

    training = payload.get("training") if isinstance(payload.get("training"), dict) else {}
    accuracy = {key: training.get(f"balanced_accuracy_{key}") for key in ("unseen_board", "unseen_session")
                if finite(training.get(f"balanced_accuracy_{key}"))}
    return {
        "name": name, "version": version, "description": description, "classes": list(classes),
        "display_classes": display_classes,
        "window_seconds": window, "warmup_seconds": warmup, "prediction_interval_seconds": interval,
        "requirements": requirements, "features": list(features), "steps_by_profile": steps_by_profile,
        "impute": impute, "mean": mean, "scale": scale, "coef": coef, "intercept": intercept,
        "unknown_below": unknown_below, "clip_ohm": clip, "references": references,
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
        "references": [{"board": board, "recorded_at": ref.get("recorded_at")} for board, ref in model["references"].items()],
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
            # measurement start
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


# board from the device name: bme688-01 is bme1 (BME_BOARD for local tests)
def board_name():
    if os.environ.get("BME_BOARD"):
        return os.environ["BME_BOARD"]
    match = re.search(r"bme688-0*(\d+)", socket.gethostname(), re.IGNORECASE)
    return f"bme{match.group(1)}" if match else None


def average(rows):
    out = []
    for i in range(10):
        values = [row[i] for row in rows if row[i] is not None]
        out.append(sum(values) / len(values) if values else None)
    return out


# clean air from training: the board's own sensors, otherwise the average of the training boards
def training_reference(model, mapping, board):
    stored = model["references"]
    own = stored[board]["ln_r0"] if board in stored else {}
    every = [(profile, values) for entry in stored.values() for (_, profile), values in entry["ln_r0"].items()]
    refs, averaged = {}, False
    for profile, sensors in mapping.items():
        for sensor in sensors:
            if (sensor, profile) in own:
                refs[sensor] = own[(sensor, profile)]
                continue
            rows = [values for (_, p), values in own.items() if p == profile] or [v for p, v in every if p == profile]
            if rows:
                refs[sensor], averaged = average(rows), True
    if board in stored and not averaged:
        label = f"Training clean air of {board}"
    elif board in stored:
        label = f"Training clean air of {board}, averaged where sensors were moved"
    else:
        label = "Average training clean air" + (f" ({board} is not in the model)" if board else "")
    complete = all(any(s in refs for s in sensors) for sensors in mapping.values())
    reference = {"source": "training", "board": board, "boards": sorted(stored), "averaged": averaged or board not in stored,
                 "log": board if board in stored and not averaged else "average",
                 "recorded_at": stored[board].get("recorded_at") if board in stored else None, "label": label}
    return (refs if complete else None), reference


# live predictor

class Predictor:
    def __init__(self, state_dir: Path, measurements_dir: Path, session_path: Path, is_running, read_config,
                 on_guide_done=None):
        self.state_dir = state_dir
        self.store = ModelStore(state_dir)
        self.data = LiveData(measurements_dir, session_path)
        self.session_path = session_path
        self.is_running, self.read_config = is_running, read_config
        self.lock = threading.Lock()
        self.result = None
        self.result_checks = []        # checks of the last prediction
        self.measured = None           # clean air from the running measurement
        self.cycle_ref = None          # clean air of the current cycle
        self.on_guide_done = on_guide_done
        self.guide_finished = False    # the last guided measurement ended on its own
        self.cycles_written = None
        self.data_session = None       # the measurement the loaded data belongs to
        self.status_cache = None
        self.last_prediction_t = None
        self.thread = None
        self.stop_event = threading.Event()

    # background loop
    def start(self):
        if self.thread:
            return
        self._recover()
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

    # after a reboot the sensors are off: keep what was recorded, marked as interrupted
    def _recover(self):
        session = read_json(self.session_path, {}) or {}
        if not session or self.is_running():
            return
        guide = session.get("guide")
        if guide:
            self.data.update()
            run = self.data.newest_run()
            if run:
                guide["interrupted"] = True
                try:
                    write_json_atomic(run / "cycles.json", guide)
                except OSError:
                    pass
        try:
            self.session_path.unlink()
        except OSError:
            pass

    def session_key(self):
        session = read_json(self.session_path, {}) or {}
        return f"{session.get('title_dir')}|{session.get('started_at')}"

    def tick(self, now_ms=None):
        now_ms = now_ms or int(time.time() * 1000)
        running = self.is_running()
        model = self.store.active()
        if not running:
            with self.lock:
                self.result, self.last_prediction_t, self.result_checks = None, None, []
                self.status_cache = self._status(model, running=False)
            return
        self.data.clip = model["clip_ohm"] if model else DEFAULT_CLIP_OHM
        self.data.update()
        self.data_session = self.session_key()
        if self._guide_tick(now_ms):
            return
        status = self._status(model, running=True, now_ms=now_ms)
        if status.get("due"):
            self._predict(model, status)
        elif status.get("state") == "ready" and self.result:
            status["checks"].extend(self.result_checks)
            status["state"] = self.result["state"]
            status["message"] = self.result.get("message", "")
        if self.last_prediction_t is not None and status.get("state") in ("ready", "unknown", "unusual", "insufficient"):
            last_t = self.data.last_t() or self.last_prediction_t
            due = self.last_prediction_t + model["prediction_interval_seconds"] * 1000
            status["next_prediction_s"] = max(0, round((due - last_t) / 1000))
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
            return status

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
        refs, reference = training_reference(model, mapping, board_name())
        session = (read_json(self.session_path, {}) or {}) if running else {}
        guide = session.get("guide")
        cycle_ref = self._cycle_reference(guide, session) if guide else None
        if cycle_ref:
            # sensors without a value keep the training value
            refs = {s: cycle_ref["refs"].get(s, (refs or {}).get(s)) for sensors in mapping.values() for s in sensors}
            reference = cycle_ref["reference"]
        elif running and not guide:
            measured = self._measured_reference()
            if measured:
                # sensors without a measured value keep the training value
                refs = {s: measured["refs"].get(s, (refs or {}).get(s)) for sensors in mapping.values() for s in sensors}
                reference = measured["reference"]
        status["reference"] = reference
        reference_check = {"id": "reference", "ok": refs is not None, "warn": reference["averaged"],
                           "label": "Clean-air reference", "detail": reference["label"]}
        if not running:
            checks.append(reference_check)
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
        status["measurement"] = {"elapsed_s": round(elapsed)}
        checks.append(reference_check)
        if not fresh:
            status.update(state="no_data", message="No new readings from the sensors. Check the measurement log.")
            return status
        if refs is None:
            status.update(state="no_reference", message="The model has no training clean air for these heater profiles.")
            return status
        # guided: collecting predicts only with the sample in place, testing after the clean air
        start = first_t
        if guide:
            g_start, g_now = self._times(session)
            allowed = guided.prediction_start(guide, g_start, g_now)
            if allowed is None or not cycle_ref:
                waiting = "waiting_sample" if cycle_ref and guide["mode"] == "collect" else "waiting_clean_air"
                status.update(state=waiting, message="")
                return status
            start = allowed
        window_ready_at = start + model["window_seconds"] * 1000
        if last_t < window_ready_at:
            status.update(state="collecting", message="Collecting the first analysis window.",
                          collecting_remaining_s=round((window_ready_at - last_t) / 1000))
            return status
        status["warnings"] = []
        interval_ms = model["prediction_interval_seconds"] * 1000
        due = self.result is None or self.last_prediction_t is None or last_t - self.last_prediction_t >= interval_ms
        status.update(state="ready", message="", due=due,
                      _ctx={"mapping": mapping, "refs": refs, "setup": setup, "reference": reference, "guide": guide})
        return status

    # sensor time of the measurement: start and now
    def _times(self, session):
        started = int((session.get("started_at") or time.time()) * 1000)
        if self.data_session != self.session_key() or not self.data.first_t:
            # data of this measurement not loaded yet
            return started, max(int(time.time() * 1000), started)
        return self.data.first_t, max(self.data.last_t() or self.data.first_t, self.data.first_t)

    # guided cycles for the interface
    def guide_state(self):
        session = read_json(self.session_path, {}) or {}
        guide = session.get("guide")
        if not guide or not self.is_running():
            return {"status": "success", "active": False, "finished": self.guide_finished}
        start, now = self._times(session)
        current = guided.phase(guide, start, now)
        return {"status": "success", "active": True, "finished": False, "mode": guide["mode"],
                "cycles": guide["cycles"], "phase": current, "now_ms": now, "start_ms": start,
                "left_ms": guided.left_ms(guide, current, now), "warm": guide["warm"],
                "warm_limit": guided.WARM_LIMIT, "warm_checks": guided.WARM_CHECKS}

    # a tap from the interface: sample placed, sample removed or new clean air
    def guide_record(self, action, index):
        session = read_json(self.session_path, {}) or {}
        guide = session.get("guide")
        if not guide or not self.is_running():
            return "No guided measurement is running."
        start, now = self._times(session)
        problem = guided.record(guide, start, now, action, index)
        if problem:
            return problem
        write_json_atomic(self.session_path, session)
        self._write_cycles(guide)
        return None

    def _write_cycles(self, guide):
        run = self.data.newest_run()
        if not run or guide == self.cycles_written:
            return
        try:
            write_json_atomic(run / "cycles.json", guide)
            self.cycles_written = guide
        except OSError:
            pass

    # keeps cycles.json next to the recording and stops after the last sample
    def _guide_tick(self, now_ms):
        session = read_json(self.session_path, {}) or {}
        guide = session.get("guide")
        if not guide:
            return False
        self.guide_finished = False
        start, now = self._times(session)
        if self.data_session == self.session_key() and guided.warm_update(guide, start, now, self.data.cycles_by_sensor()):
            write_json_atomic(self.session_path, session)
        if guide["mode"] == "test" or guided.phase(guide, start, now)["key"] != "done":
            self._write_cycles(guide)
            return False
        self.guide_finished = True
        if guide.get("finished"):
            return True
        guide["finished"] = True
        write_json_atomic(self.session_path, session)
        self._write_cycles(guide)
        if self.on_guide_done:
            self.on_guide_done()
        with self.lock:
            self.status_cache = self._status(self.store.active(), running=False)
        return True

    # clean air of the newest cycle whose clean-air phase is over
    def _cycle_reference(self, guide, session):
        start, now = self._times(session)
        done = [w for w in guided.clean_windows(guide) if w[2] <= now]
        if not done:
            return None
        index, low, high = done[-1]
        key = (self.session_key(), index)
        if self.cycle_ref and self.cycle_ref["key"] == key:
            return self.cycle_ref
        refs = {}
        for sensor, items in self.data.cycles_by_sensor().items():
            inside = [c for c in items if low <= c[0] < high]
            if len(inside) >= 3:
                refs[sensor] = [median([c[1][i] for c in inside]) for i in range(10)]
        if not refs:
            return None
        name = "clean air" if guide["mode"] == "test" else "cycle"
        reference = {"source": "cycle", "board": None, "boards": [], "averaged": False, "log": f"{name.replace(' ', '_')}{index + 1}",
                     "cycle": index + 1, "recorded_at": iso(high), "label": f"Clean air {index + 1}"}
        self.cycle_ref = {"key": key, "refs": refs, "reference": reference, "ready_at": high}
        run = self.data.newest_run()
        if run:
            try:
                write_json_atomic(run / f"reference_cycle{index + 1}.json", {
                    "cycle": index + 1, "from": iso(low), "to": iso(high),
                    "ln_r0": {str(sensor): values for sensor, values in refs.items()}})
            except OSError:
                pass
        return self.cycle_ref

    # clean air from minutes 10-15 of the running measurement, taken once per measurement
    def _measured_reference(self):
        key = self.session_key()
        if self.measured and self.measured["key"] == key:
            return self.measured
        first_t, last_t = self.data.first_t, self.data.last_t()
        if not first_t or not last_t or last_t < first_t + MEASURED_REFERENCE_TO_S * 1000:
            return None
        low, high = first_t + MEASURED_REFERENCE_FROM_S * 1000, first_t + MEASURED_REFERENCE_TO_S * 1000
        middle = (low + high) / 2
        refs, changes = {}, {}
        for sensor, items in self.data.cycles_by_sensor().items():
            inside = [c for c in items if low <= c[0] < high]
            if len(inside) < 3:
                continue
            refs[sensor] = [median([c[1][i] for c in inside]) for i in range(10)]
            first = [median([c[1][i] for c in inside if c[0] < middle]) for i in range(10)]
            second = [median([c[1][i] for c in inside if c[0] >= middle]) for i in range(10)]
            changes[sensor] = round(median([abs(a - b) for a, b in zip(first, second) if a is not None and b is not None]) or 0, 4)
        if not refs:
            return None
        reference = {"source": "measurement", "board": None, "boards": [], "averaged": False, "log": "measurement",
                     "recorded_at": iso(high), "label": "Clean air from minutes 10-15 of this measurement"}
        self.measured = {"key": key, "refs": refs, "reference": reference}
        # kept next to the recording, to compare later
        run = self.data.newest_run()
        if run:
            try:
                write_json_atomic(run / "reference.json", {
                    "from": iso(low), "to": iso(high), "change_between_halves": changes,
                    "ln_r0": {str(sensor): values for sensor, values in refs.items()}})
            except OSError:
                pass
        return self.measured

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
        guide = ctx.get("guide")
        if guide and guide["warm"].get("capped"):
            notes.append("The sensors were still settling when the 20 min warm-up limit was reached.")
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
            "reference": ctx["reference"]["log"], "warnings": list(status["warnings"]),
            "expected": guided.expected(ctx["guide"], t_end) if ctx.get("guide") else {},
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
                                 + ",".join(f"P_{c}" for c in classes) + ",Reference,Missing_values,Unusual_values,Warnings,"
                                 + "Sample,Expected\n")
                probs = {p["class"]: p["p"] for p in result["probabilities"]}
                warnings = " | ".join(result["warnings"]).replace('"', "'")
                expected = result.get("expected") or {}
                sample = str(expected.get("object", "")).replace('"', "'")
                handle.write(f"{result['t_ms']},{result['time']},\"{model['name']}\",{model['version']},{result['state']},"
                             f"{result['label'] or ''},{result['confidence']:.4f},"
                             + ",".join(f"{probs[c]:.4f}" for c in classes)
                             + f",{result['reference']},{result['missing_features']},{result['unusual_features']},\"{warnings}\""
                             + f",\"{sample}\",{expected.get('sample_class') or ''}\n")
        except OSError:
            pass


def median(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2
