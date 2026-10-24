# device classifier with synthetic data
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
CLEAN = 14.0       # ln R of clean air
T0 = 1_790_000_000_000


def make_payload():
    features = [f"{p}_shape_s{i}" for p, steps in STEPS.items() for i in steps]
    n = len(features)
    return {
        "metadata": {
            "name": "Test model", "version": "1.0", "classes": ["clean_air", "smell"],
            "window_seconds": 120, "warmup_seconds": 900, "prediction_interval_seconds": 30,
            "profile_requirements": [
                {"profile": p, "count": 2, "mode": "parallel", "duty": 0, "sleep": 0, "temps": t, "ticks": k}
                for p, (t, k) in PROFILES.items()
            ],
        },
        "model": {
            "type": "multinomial_logistic_regression", "features": features,
            "use_steps": STEPS, "impute_median": [0.0] * n, "scaler_mean": [0.0] * n, "scaler_scale": [0.1] * n,
            "classes": ["clean_air", "smell"],
            # a higher first step means smell
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
        # the device name says which board this is
        self.board = patch.object(classifier, "board_name", return_value="bme7").start()
        self.addCleanup(patch.stopall)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, minutes, shift=0.0, drift=0.0):
        # shift is a smell, drift is a sensor still changing
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
            (lambda d: d.pop("clean_air_reference"), "no clean_air_reference"),
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
        self.write(1)
        status = self.tick()
        self.assertEqual(status["state"], "collecting")

        # first prediction after one window, no warm-up
        self.write(1.5)
        status = self.tick()
        self.assertEqual(status["reference"]["board"], "bme7")
        self.assertFalse(status["reference"]["averaged"])
        self.assertEqual(status["result"]["label"], "clean_air")
        self.assertEqual(status["warnings"], [])
        self.assertTrue(0 <= status["next_prediction_s"] <= 30)

        self.write(2.5, shift=0.3)
        status = self.tick()
        self.assertEqual(status["result"]["label"], "smell")
        log = (self.run_dir / "predictions.csv").read_text().splitlines()
        self.assertTrue(log[0].startswith("Timestamp_ms,Time,Model"))
        self.assertIn(",smell,", log[-1])
        self.assertIn(",bme7,", log[-1])

    def start_guided(self, mode, cycles=()):
        classifier.write_json_atomic(self.session, {"title_dir": str(self.run_dir.parent), "started_at": T0 / 1000,
                                                    "guide": classifier.guided.new_guide(mode, list(cycles))})

    def measure(self, minutes, shift=0.0, drift=0.0):
        # the device checks every few seconds, here every half minute
        status = None
        for _ in range(round(minutes * 2)):
            self.write(0.5, shift=shift, drift=drift)
            status = self.tick()
        return status

    def phase(self):
        return self.predictor.guide_state()["phase"]["key"]

    def test_collect_predicts_only_with_the_sample_in_place(self):
        cycles = [{"object": "Scortisoara", "sample_class": "spicy", "distance_cm": 5, "notes": ""},
                  {"object": "Sosete", "sample_class": "bad_smell", "distance_cm": 10, "notes": ""}]
        self.start_guided("collect", cycles)
        done = []
        self.predictor.on_guide_done = lambda: done.append(True)

        # steady sensors are warm after 3 min of data and 3 checks
        self.assertEqual(self.measure(3.5, shift=0.3)["state"], "waiting_clean_air")
        self.assertEqual(self.phase(), "warmup")
        self.measure(1, shift=0.3)
        self.assertEqual(self.phase(), "clean")

        # clean air done: still no prediction until the sample is placed
        status = self.measure(3, shift=0.3)
        self.assertEqual(self.phase(), "add")
        self.assertEqual(status["state"], "waiting_sample")
        self.measure(2, shift=0.3)
        self.assertIsNone(self.predictor.result)

        # taps at the wrong moment are refused
        self.assertIsNotNone(self.predictor.guide_record("removed", 0))
        self.assertIsNone(self.predictor.guide_record("placed", 0))
        self.assertIsNotNone(self.predictor.guide_record("placed", 0))

        # one window after the tap, compared with the clean air of cycle 1
        self.assertEqual(self.measure(1.5, shift=0.6)["state"], "collecting")
        status = self.measure(1, shift=0.6)
        self.assertEqual(status["reference"]["log"], "cycle1")
        self.assertEqual(status["result"]["label"], "smell")
        self.assertTrue(status["result"]["t_ms"] - 120000 >= self.predictor.guide_state()["phase"]["start_ms"])
        self.assertTrue(self.run_dir.joinpath("predictions.csv").read_text().splitlines()[-1].endswith(',"Scortisoara",spicy'))
        self.measure(7.5, shift=0.6)
        self.assertEqual(self.phase(), "remove")
        self.assertIsNone(self.predictor.guide_record("removed", 0))

        # second cycle: airing out and new clean air, no prediction until the next tap
        status = self.measure(6.5, shift=0.3)
        self.assertEqual(self.phase(), "add")
        self.assertEqual(status["state"], "waiting_sample")
        self.assertEqual(self.predictor.cycle_ref["reference"]["log"], "cycle2")
        self.assertIsNone(self.predictor.guide_record("placed", 1))
        self.measure(10.5, shift=0.3)
        self.assertEqual(done, [True])
        saved = json.loads((self.run_dir / "cycles.json").read_text())
        self.assertTrue(saved["finished"])
        self.assertEqual(len(saved["placed"]), 2)
        self.assertIsNotNone(saved["warm_at"])

    def test_test_mode_predicts_after_the_clean_air_until_stop(self):
        self.start_guided("test")
        self.measure(4.5, shift=0.3)
        self.assertEqual(self.phase(), "clean")
        self.assertIsNotNone(self.predictor.guide_record("reclean", 0))
        status = self.measure(3.5, shift=0.3)
        self.assertEqual(self.phase(), "testing")
        self.assertEqual(status["state"], "collecting")
        status = self.measure(2, shift=0.3)
        self.assertEqual(status["result"]["label"], "clean_air")
        self.assertEqual(status["reference"]["log"], "clean_air1")

        # new clean air: predictions pause, then use the new reference
        self.assertIsNone(self.predictor.guide_record("reclean", 0))
        status = self.measure(1, shift=0.6)
        self.assertEqual((self.phase(), status["state"]), ("clean", "waiting_clean_air"))
        self.measure(4.5, shift=0.6)
        self.assertEqual(self.predictor.cycle_ref["reference"]["log"], "clean_air2")
        self.assertEqual(self.predictor.result["label"], "clean_air")
        self.assertTrue(self.predictor.is_running())
        self.assertTrue(self.run_dir.joinpath("predictions.csv").read_text().splitlines()[-1].endswith(',"",'))

    def test_warm_up_waits_for_the_sensors_to_settle(self):
        start, data = T0, {1: []}
        guide = classifier.guided.new_guide("test", [])
        # ln R falls fast after a cold start, then settles
        for k in range(0, 25 * 60, 10):
            data[1].append((start + k * 1000, [14.0 - 1.5 * math.exp(-k / 150)] * 10))
            classifier.guided.warm_update(guide, start, start + k * 1000, data)
            if guide["warm_at"]:
                break
        minutes = (guide["warm_at"] - start) / 60000
        self.assertTrue(7 <= minutes <= 12, minutes)
        self.assertFalse(guide["warm"]["capped"])

        # sensors that never settle: go on after 20 min, with a note
        guide = classifier.guided.new_guide("test", [])
        data = {1: [(start + k * 1000, [14.0 - k / 600] * 10) for k in range(0, 21 * 60, 10)]}
        for k in range(0, 21 * 60, 3):
            classifier.guided.warm_update(guide, start, start + k * 1000, data)
        self.assertTrue(guide["warm"]["capped"])
        self.assertEqual(guide["warm_at"], start + 20 * 60 * 1000)

    def test_a_capped_warm_up_is_noted_with_every_prediction(self):
        self.start_guided("test")
        session = json.loads(self.session.read_text())
        session["guide"]["warm"]["capped"] = True
        session["guide"]["warm_at"] = T0 + 60000
        classifier.write_json_atomic(self.session, session)
        self.measure(6.5)
        self.assertEqual(self.predictor.result["label"], "clean_air")
        self.assertIn("20 min", (self.run_dir / "predictions.csv").read_text().splitlines()[-1])

    def test_a_reboot_marks_the_recording_as_interrupted(self):
        self.start_guided("collect", [{"object": "Chili", "sample_class": "spicy", "distance_cm": 5, "notes": ""}])
        self.write(2)
        self.running = False
        self.predictor._recover()
        saved = json.loads((self.run_dir / "cycles.json").read_text())
        self.assertTrue(saved["interrupted"])
        self.assertFalse(self.session.exists())

    def test_unknown_board_uses_the_training_average(self):
        self.board.return_value = None
        self.write(2.5)
        status = self.tick()
        self.assertTrue(status["reference"]["averaged"])
        self.assertEqual(status["result"]["label"], "clean_air")

    def test_moved_sensors_use_the_board_average(self):
        self.config["assignments"] = {"1": "p1", "2": "p1", "3": "p0", "4": "p0"}
        self.write(2.5)
        status = self.tick()
        self.assertIn("averaged", status["reference"]["label"])
        self.assertEqual(status["result"]["label"], "clean_air")

    def test_profile_mismatch_stops_predictions(self):
        self.config = make_config(swap_ticks=True)
        self.write(1)
        status = self.tick()
        self.assertEqual(status["state"], "incompatible")
        self.assertIn("hp_301_basic", status["issues"][0])

    def test_unusual_pattern_is_not_classified(self):
        self.write(1)
        self.write(2.5, shift=3.0)
        status = self.tick()
        self.assertEqual(status["result"]["state"], "unusual")
        self.assertIsNone(status["result"]["label"])

    def test_stale_data_is_reported(self):
        self.write(3)
        self.predictor.tick(now_ms=self.t + 10 * 60000)
        self.assertEqual(self.predictor.snapshot()["state"], "no_data")

    def test_stopping_clears_the_prediction(self):
        self.write(3)
        self.tick()
        self.running = False
        status = self.tick()
        self.assertEqual(status["state"], "idle")
        self.assertIsNone(status["result"])


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
