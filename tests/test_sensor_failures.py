# the sensor program failing at start or during a measurement, with small fake programs
import json
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import classifier, main


def fake_program(folder: Path, body: str) -> str:
    path = folder / "fake_sensor.sh"
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


class SensorFailures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        session = self.root / "session.json"
        patches = {
            "MEASUREMENTS_DIR": self.root / "measurements", "SESSION_PATH": session,
            "LOG_PATH": self.root / "bme_log.txt", "GUIDE_SETTINGS_PATH": self.root / "guide.json",
            "START_WAIT_S": 3, "CONFIG_PATH": self.root / "config.json",
        }
        for name, value in patches.items():
            patch.object(main, name, value).start()
        patch.object(main.predictor, "session_path", session).start()
        patch.object(main.predictor.data, "session_path", session).start()
        patch.object(main.predictor.data, "root", self.root / "measurements").start()
        self.addCleanup(patch.stopall)
        main.sensor_process, main.last_failure = None, None

    def tearDown(self):
        if main.sensor_process and main.sensor_process.poll() is None:
            main.sensor_process.kill()
        main.sensor_process = None
        self.temp.cleanup()

    def start(self, body, mode=None):
        main.SENSOR_COMMAND = fake_program(self.root, body)
        request = main.StartSensorRequest(measurement_title="Test", mode=mode)
        return main.start_sensor(request)

    def test_a_dead_sensor_board_stops_the_start_with_a_reason(self):
        result = self.start('echo "[FATAL] Sensor 3 does not answer."; exit 3')
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], "board")
        self.assertIn("Sensor 3", result["message"])
        self.assertIsNone(main.sensor_process)
        self.assertFalse(main.SESSION_PATH.exists())

    def test_bus_and_storage_problems_have_their_own_reason(self):
        self.assertEqual(self.start('echo "[FATAL] SPI is off"; exit 4')["error"], "bus")
        self.assertEqual(self.start('echo "[FATAL] Failed to create file set folder"; exit 5')["error"], "files")
        self.assertEqual(self.start('exit 1')["error"], "crashed")

    def test_a_program_that_ends_during_a_measurement_shows_once(self):
        result = self.start('echo "[START] ok"; sleep 1; echo "[FATAL] Could not write the readings of sensor 2."; exit 5',
                            mode="test")
        self.assertEqual(result["status"], "success")
        self.assertTrue(main.get_status()["is_running"])
        time.sleep(1.5)
        status = main.get_status()
        self.assertFalse(status["is_running"])
        self.assertEqual(status["failure"]["code"], "files")
        self.assertIn("sensor 2", status["failure"]["message"])
        self.assertFalse(main.SESSION_PATH.exists())
        # the next start clears it
        self.start('echo "[START] ok"; sleep 5')
        self.assertIsNone(main.get_status()["failure"])

    def test_pressing_stop_is_not_a_failure(self):
        self.start('trap "exit 0" TERM; echo "[START] ok"; while true; do sleep 0.1; done')
        self.assertEqual(main.stop_sensor()["status"], "success")
        self.assertIsNone(main.get_status()["failure"])


class CycleSplitting(unittest.TestCase):
    def write(self, steps):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "sensor1_hp_354_standard.csv"
        rows = ["Timestamp_ms,Sensor,Gas_Index,Target_Temp_C,Gas_Resistance_Ohms,Temperature_C,Humidity_perc,Pressure_hPa"]
        rows += [f"{1000 + k * 100},1,{step},300,{50000 + step},25,40,1000" for k, step in enumerate(steps)]
        path.write_text("\n".join(rows) + "\n")
        reader = classifier.CycleReader(path, 1.02e8)
        reader.update()
        return reader.complete_cycles()

    def test_a_missing_step_skips_only_that_cycle(self):
        full = list(range(10))
        # step 0 missing in the second cycle: it must not be glued to the first one
        cycles = self.write(full + list(range(1, 10)) + full)
        self.assertEqual(len(cycles), 2)
        self.assertEqual([round(v, 6) for v in cycles[0][1]], [round(__import__("math").log(50000 + s), 6) for s in full])
        # a step missing in the middle
        self.assertEqual(len(self.write(full + [0, 1, 2, 4, 5, 6, 7, 8, 9] + full)), 2)


if __name__ == "__main__":
    unittest.main()
