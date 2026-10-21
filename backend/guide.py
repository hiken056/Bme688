# guided measurement on the device
# collect: warm-up, then clean air, sample, remove for each sample, stops on its own
# test: warm-up, clean air, then predictions until stop, new clean air when asked
# times are in sensor time (ms), so a replay at higher speed runs faster too
CLEAN_S = 180
SAMPLE_S = 600
SETTLE_S = 180       # airing out after a sample

# warm-up: the worst sensor changes less than WARM_LIMIT per minute, WARM_CHECKS checks in a row
WARM_LIMIT = 0.05
WARM_CHECKS = 3
WARM_EVERY_S = 30
WARM_HALF_S = 90     # compares the last 1.5 min with the 1.5 min before
WARM_MAX_S = 1200    # never waits longer than 20 min


def new_guide(mode: str, cycles: list) -> dict:
    return {"mode": mode, "cycles": cycles, "placed": [], "removed": [], "recleans": [], "warm_at": None,
            "warm": {"checks": 0, "last_check": None, "drift": None, "capped": False}, "finished": False}


def _at(values, index):
    return values[index] if index < len(values) else None


def _median(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2


# drift per minute of the worst sensor: typical change of ln R over the heater steps
def drift(cycles_by_sensor: dict, now_ms: int):
    half = WARM_HALF_S * 1000
    worst = None
    for items in cycles_by_sensor.values():
        recent = [c[1] for c in items if now_ms - half < c[0] <= now_ms]
        before = [c[1] for c in items if now_ms - 2 * half < c[0] <= now_ms - half]
        if not recent or not before:
            continue
        changes = []
        for step in range(10):
            a, b = _median([c[step] for c in recent]), _median([c[step] for c in before])
            if a is not None and b is not None:
                changes.append(abs(a - b))
        change = _median(changes)
        if change is not None:
            per_minute = change / (WARM_HALF_S / 60)
            worst = per_minute if worst is None else max(worst, per_minute)
    return worst


# one warm-up check every WARM_EVERY_S, returns True when the guide changed
def warm_update(guide: dict, start_ms: int, now_ms: int, cycles_by_sensor: dict) -> bool:
    if guide["warm_at"] is not None:
        return False
    warm = guide["warm"]
    if now_ms - start_ms >= WARM_MAX_S * 1000:
        guide["warm_at"], warm["capped"] = now_ms, True
        return True
    if now_ms - start_ms < 2 * WARM_HALF_S * 1000:
        return False
    if warm["last_check"] is not None and now_ms - warm["last_check"] < WARM_EVERY_S * 1000:
        return False
    value = drift(cycles_by_sensor, now_ms)
    if value is None:
        return False
    warm["last_check"], warm["drift"] = now_ms, round(value, 3)
    warm["checks"] = warm["checks"] + 1 if value < WARM_LIMIT else 0
    if warm["checks"] >= WARM_CHECKS:
        guide["warm_at"] = now_ms
    return True


# start of every clean-air phase known so far
def clean_starts(guide: dict) -> list:
    if guide["warm_at"] is None:
        return []
    if guide["mode"] == "test":
        return [guide["warm_at"]] + guide["recleans"]
    starts = [guide["warm_at"]] + [t + SETTLE_S * 1000 for t in guide["removed"]]
    return starts[:len(guide["cycles"])]


def clean_windows(guide: dict) -> list:
    return [(i, low, low + CLEAN_S * 1000) for i, low in enumerate(clean_starts(guide))]


def phase(guide: dict, start_ms: int, now_ms: int) -> dict:
    if guide["warm_at"] is None:
        return {"key": "warmup", "index": 0, "start_ms": start_ms}
    windows = clean_windows(guide)
    if guide["mode"] == "test":
        index, low, high = windows[-1]
        if now_ms < high:
            return {"key": "clean", "index": index, "start_ms": low, "end_ms": high}
        return {"key": "testing", "index": index, "since_ms": high}
    n = len(guide["cycles"])
    for i in range(n):
        if i > 0 and now_ms < guide["removed"][i - 1] + SETTLE_S * 1000:
            removed = guide["removed"][i - 1]
            return {"key": "settle", "index": i, "start_ms": removed, "end_ms": removed + SETTLE_S * 1000}
        _, low, high = windows[i]
        if now_ms < high:
            return {"key": "clean", "index": i, "start_ms": low, "end_ms": high}
        placed = _at(guide["placed"], i)
        if placed is None:
            return {"key": "add", "index": i, "since_ms": high}
        if now_ms < placed + SAMPLE_S * 1000:
            return {"key": "sample", "index": i, "start_ms": placed, "end_ms": placed + SAMPLE_S * 1000}
        # the last sample needs no tap, the measurement stops on its own
        if i < n - 1 and _at(guide["removed"], i) is None:
            return {"key": "remove", "index": i, "since_ms": placed + SAMPLE_S * 1000}
    return {"key": "done", "index": n - 1}


# planned time still to go after the warm-up, None for tests
def left_ms(guide: dict, current: dict, now_ms: int):
    if guide["mode"] == "test":
        return None
    n = len(guide["cycles"])
    if current["key"] == "done":
        return 0
    if current["key"] == "warmup":
        return (n * (CLEAN_S + SAMPLE_S) + (n - 1) * SETTLE_S) * 1000
    left = max(0, current["end_ms"] - now_ms) if current.get("end_ms") else 0
    after = {"settle": CLEAN_S + SAMPLE_S, "clean": SAMPLE_S, "add": SAMPLE_S, "sample": 0, "remove": 0}
    left += after[current["key"]] * 1000
    left += (n - 1 - current["index"]) * (SETTLE_S + CLEAN_S + SAMPLE_S) * 1000
    return left


# predictions use data from here on: after the tap, or after the clean air in a test
def prediction_start(guide: dict, start_ms: int, now_ms: int):
    current = phase(guide, start_ms, now_ms)
    if guide["mode"] == "test":
        return current["since_ms"] if current["key"] == "testing" else None
    if current["key"] in ("sample", "remove"):
        return guide["placed"][current["index"]]
    return None


# the sample in front of the sensors at t_ms, for the prediction log
def expected(guide: dict, t_ms: int) -> dict:
    if guide["mode"] == "test":
        return {}
    for i, placed in enumerate(guide["placed"]):
        end = _at(guide["removed"], i) or placed + SAMPLE_S * 1000
        if placed <= t_ms <= end:
            return guide["cycles"][i]
    return {}


# a tap is only accepted in the step that asks for it
def record(guide: dict, start_ms: int, now_ms: int, action: str, index: int):
    current = phase(guide, start_ms, now_ms)
    wanted = {"placed": "add", "removed": "remove", "reclean": "testing"}[action]
    if current["key"] != wanted or (action != "reclean" and current["index"] != index):
        return "This step is already done or not reached yet."
    guide["recleans" if action == "reclean" else action].append(now_ms)
    return None
