"""Device classifier: model checks, storage, clean air and the prediction states (synthetic data)."""
import copy
import json
import math
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from backend import classifier, main

PROFILES = {
    "hp_301_basic": ([100, 100, 200, 200, 200, 200, 320, 320, 320, 320], [2, 41, 2, 14, 14, 14, 2, 14, 14, 14]),
    "hp_354_standard": ([320, 100, 100, 100, 200, 200, 200, 320, 320, 320], [5, 2, 10, 30, 5, 5, 5, 5, 5, 5]),
}
STEPS = {"hp_301_basic": [2, 3, 4, 5], "hp_354_standard": [0, 4, 5, 6]}
CLEAN = 14.0       # ln R of clean air at every step
T0 = 1_790_000_000_000


def make_payload():
    features = [f"{p}_shape_s{i}" for p, steps in STEPS.items() for i in steps]
    n = len(features)
    return {
        "metadata": {
            "name": "Test model", "version": "1.0", "classes": ["clean_air", "smell"],
            "window_seconds": 120, "warmup_seconds": 600, "prediction_interval_seconds": 30,
            "profile_requirements": [
                {"profile": p, "count": 2, "mode": "parallel", "duty": 0, "sleep": 0, "temps": t, "ticks": k}
                for p, (t, k) in PROFILES.items()
            ],
        },
        "model": {
            "type": "multinomial_logistic_regression", "features": features,
            "use_steps": STEPS, "impute_median": [0.0] * n, "scaler_mean": [0.0] * n, "scaler_scale": [0.1] * n,
            "classes": ["clean_air", "smell"],
            # binary model: one row; a bent curve (first step higher than the rest) means "smell"
            "coef": [[2.0 if i % 4 == 0 else -0.6 for i in range(n)]], "intercept": [-1.0], "unknown_below": 0.6,
        },
        "clean_air_reference": {"bme7": {"recorded_at": "2026-09-22T10:00:00", "ln_r0": {
            f"sensor{s}_{p}": [CLEAN] * 10 for s, p in [(1, "hp_301_basic"), (2, "hp_301_basic"),
                                                        (3, "hp_354_standard"), (4, "hp_354_standard")]}}},
    }


def make_config(swap_ticks=False):
    presets = []
    for index, (name, (temps, ticks)) in enumerate(PROFILES.items()):
        ticks = list(ticks)
        if swap_ticks and index == 0:
            ticks[0] += 1
        presets.append({"id": f"p{index}", "name": name, "mode": "parallel", "duty": 0, "sleep": 0,
                        "temps": temps, "ticks": ticks})
    return {"presets": presets, "assignments": {"1": "p0", "2": "p0", "3": "p1", "4": "p1"}}


class DeviceTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.state, self.measurements = root / "state", root / "measurements"
        self.session = root / "session.json"
        self.run_dir = self.measurements / "Test" / datetime.fromtimestamp(T0 / 1000).strftime("%Y-%m-%d_%H-%M-%S_30min")
        self.run_dir.mkdir(parents=True)
        classifier.write_json_atomic(self.session, {"title_dir": str(self.run_dir.parent), "started_at": T0 / 1000})
        self.running = True
        self.config = make_config()
        self.predictor = classifier.Predictor(self.state, self.measurements, self.session,
                                              lambda: self.running, lambda: self.config)
        model = self.predictor.store.add(make_payload(), "test.json", 100)
        self.predictor.store.set_active(model["id"])
        self.t = T0

    def tearDown(self):
        self.temp.cleanup()

    def write(self, minutes, shift=0.0, drift=0.0):
        """Append `minutes` of cycles to every sensor; `shift` lowers ln R at the first used step (a smell),
        `drift` lowers it at every step (sensors still changing)."""
        end = self.t + minutes * 60000
        while self.t < end:
            for sensor, profile in [(1, "hp_301_basic"), (2, "hp_301_basic"), (3, "hp_354_standard"), (4, "hp_354_standard")]:
                path = self.run_dir / f"sensor{sensor}_{profile}.csv"
                new = not path.exists()
                with path.open("a") as handle:
                    if new:
                        handle.write("Timestamp_ms,Sensor,Gas_Index,Target_Temp_C,Gas_Resistance_Ohms,Temperature_C,Humidity_perc,Pressure_hPa\n")
                    for step in range(10):
                        ln_r = CLEAN - drift - (shift if step == STEPS[profile][0] else 0.0)
                        handle.write(f"{self.t + step * 100},{sensor},{step},300,{math.exp(ln_r):.2f},25,40,1000\n")
            self.t += 10000
        return self.t

    def tick(self):
        self.predictor.tick(now_ms=self.t)
        return self.predictor.snapshot()


class ModelChecks(unittest.TestCase):
    def test_valid_model_passes(self):
        model = classifier.validate_model(make_payload())
        self.assertEqual(model["classes"], ["clean_air", "smell"])
        self.assertEqual(model["steps_by_profile"]["hp_301_basic"], [2, 3, 4, 5])

    def test_broken_models_are_rejected_with_a_reason(self):
        cases = [
            (lambda d: d.pop("model"), "no model section"),
            (lambda d: d["model"].update(type="random_forest"), "not supported"),
            (lambda d: d["model"]["scaler_scale"].pop(), "scaler_scale"),
            (lambda d: d["model"]["coef"][0].append(1.0), "coef"),
            (lambda d: d["model"]["features"].__setitem__(0, "hp_301_basic_strength"), "cannot be computed"),
            (lambda d: d["model"]["features"].__setitem__(0, "hp_999_x_shape_s1"), "not in profile_requirements"),
            (lambda d: d["metadata"].update(classes=["a", "b"]), "classes"),
            (lambda d: d["metadata"].pop("profile_requirements"), "profile_requirements"),
            (lambda d: d["metadata"]["profile_requirements"][0].update(count=7), "more than 8"),
            (lambda d: d["model"]["impute_median"].__setitem__(0, float("nan")), "impute_median"),
        ]
        for change, message in cases:
            payload = copy.deepcopy(make_payload())
            change(payload)
            with self.subTest(message=message), self.assertRaises(classifier.ModelError) as error:
                classifier.validate_model(payload)
            self.assertIn(message, str(error.exception))

    def test_class_order_follows_the_weights(self):
        payload = make_payload()
        payload["metadata"]["classes"] = ["smell", "clean_air"]
        model = classifier.validate_model(payload)
        self.assertEqual(model["classes"], ["clean_air", "smell"])
        self.assertEqual(model["display_classes"], ["smell", "clean_air"])


class StorageTests(DeviceTestCase):
    def test_store_keeps_models_and_the_active_one(self):
        store = self.predictor.store
        self.assertEqual(len(store.models()), 1)
        with self.assertRaises(classifier.ModelError):
            store.add(make_payload(), "again.json", 100)
        other = make_payload()
        other["metadata"]["name"] = "Other"
        added = store.add(other, "other.json", 100)
        store.set_active(added["id"])
        self.assertEqual(store.active()["name"], "Other")
        store.remove(added["id"])
        self.assertIsNone(store.active_id())
        self.assertEqual(len(classifier.ModelStore(self.state).models()), 1)


class PredictionStates(DeviceTestCase):
    def test_full_flow(self):
        self.write(2)
        status = self.tick()
        self.assertEqual(status["state"], "warming")
        self.assertGreater(status["measurement"]["warmup_remaining_s"], 0)

        self.write(9)
        self.assertEqual(self.tick()["state"], "no_reference")

        # clean air recorded on the board
        self.predictor.start_recording(300)
        self.assertEqual(self.tick()["state"], "calibrating")
        self.write(5.5)
        status = self.tick()
        self.assertTrue(status["calibration"]["last"]["ok"], status["calibration"])
        self.assertEqual(status["calibration"]["saved"]["sensors"], [1, 2, 3, 4])

        self.write(2.5)
        status = self.tick()
        self.assertEqual(status["reference"]["source"], "device")
        self.assertEqual(status["result"]["label"], "clean_air")
        self.assertTrue(all(check["ok"] for check in status["checks"]), status["checks"])

        self.write(2.5, shift=0.3)
        status = self.tick()
        self.assertEqual(status["result"]["label"], "smell")
        log = (self.run_dir / "predictions.csv").read_text().splitlines()
        self.assertTrue(log[0].startswith("Timestamp_ms,Time,Model"))
        self.assertIn(",smell,", log[-1])

    def test_model_reference_is_used_when_the_board_is_known(self):
        self.write(13)
        self.assertEqual(self.tick()["state"], "no_reference")
        self.predictor.set_board_name("bme7")
        status = self.tick()
        self.assertEqual(status["reference"]["source"], "model")
        self.assertEqual(status["result"]["label"], "clean_air")
        reference_check = [c for c in status["checks"] if c["id"] == "reference"][0]
        self.assertTrue(reference_check["ok"] and reference_check["warn"])

    def test_warmup_can_be_skipped(self):
        self.predictor.set_board_name("bme7")
        self.write(3)
        self.assertEqual(self.tick()["state"], "warming")
        self.predictor.skip_warmup()
        status = self.tick()
        self.assertEqual(status["result"]["label"], "clean_air")
        self.assertTrue(status["measurement"]["warmup_skipped"])

    def test_profile_mismatch_stops_predictions(self):
        self.config = make_config(swap_ticks=True)
        self.write(1)
        status = self.tick()
        self.assertEqual(status["state"], "incompatible")
        self.assertIn("hp_301_basic", status["issues"][0])

    def test_unusual_pattern_is_not_classified(self):
        self.predictor.set_board_name("bme7")
        self.write(11)
        self.write(2.5, shift=3.0)
        status = self.tick()
        self.assertEqual(status["result"]["state"], "unusual")
        self.assertIsNone(status["result"]["label"])

    def test_stale_data_is_reported(self):
        self.predictor.set_board_name("bme7")
        self.write(12)
        self.predictor.tick(now_ms=self.t + 10 * 60000)
        self.assertEqual(self.predictor.snapshot()["state"], "no_data")

    def test_unstable_clean_air_gets_a_warning(self):
        self.write(11)
        self.tick()
        self.predictor.start_recording(300)
        self.write(2.5)
        self.write(3, drift=0.4)
        status = self.tick()
        self.assertTrue(status["calibration"]["last"]["ok"])
        self.assertTrue(any("changed" in w for w in status["calibration"]["last"]["warnings"]))

    def test_stopping_cancels_a_recording(self):
        self.write(11)
        self.predictor.start_recording(300)
        self.running = False
        status = self.tick()
        self.assertEqual(status["state"], "idle")
        self.assertFalse(status["calibration"]["last"]["ok"])
        with self.assertRaises(ValueError):
            self.predictor.start_recording(300)


class ApiLocks(unittest.TestCase):
    @patch.object(main, "sensor_is_running", return_value=True)
    def test_models_cannot_change_while_measuring(self, _running):
        self.assertEqual(main.select_model(main.ActiveModelRequest(id=None)).status_code, 409)
        self.assertEqual(main.delete_model("anything").status_code, 409)

    @patch.object(main, "sensor_is_running", return_value=True)
    def test_clock_is_not_moved_while_measuring(self, _running):
        with patch.object(main.subprocess, "run") as run:
            self.assertEqual(main.sync_time(main.TimeSync(timestamp_ms=0))["status"], "skipped")
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
