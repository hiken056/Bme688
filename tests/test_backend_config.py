import copy
import json
from pathlib import Path
import tempfile
import unittest

from backend import main


class ConfigPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.original_config_path = main.CONFIG_PATH
        main.CONFIG_PATH = root / "var" / "lib" / "bme688" / "config.json"

        self.payload = {
            "file_duration_minutes": 25,
            "presets": [
                {
                    "id": "field_profile",
                    "name": "Field profile",
                    "mode": "sequential",
                    "duty": 2,
                    "sleep": 30,
                    "temps": [200] * 10,
                    "ticks": [10] * 10,
                }
            ],
            "assignments": {str(i): "field_profile" for i in range(1, 9)},
        }

    def tearDown(self):
        main.CONFIG_PATH = self.original_config_path
        self.temp_dir.cleanup()

    def test_save_config_writes_durable_file_and_reads_it_back(self):
        response = main.save_config(copy.deepcopy(self.payload))

        self.assertEqual(response, {"status": "success"})
        self.assertTrue(main.CONFIG_PATH.exists())
        self.assertEqual(main.get_config(), self.payload)
        self.assertEqual(json.loads(main.CONFIG_PATH.read_text()), self.payload)
        self.assertEqual(list(main.CONFIG_PATH.parent.glob(".config.json.*")), [])

    def test_old_sleep_key_is_normalized(self):
        old_payload = copy.deepcopy(self.payload)
        old_preset = old_payload["presets"][0]
        old_preset["sleep_sec"] = old_preset.pop("sleep")
        main.CONFIG_PATH.parent.mkdir(parents=True)
        main.CONFIG_PATH.write_text(json.dumps(old_payload))

        self.assertEqual(main.read_config(), self.payload)

    def test_old_config_gets_the_default_file_duration(self):
        old_payload = copy.deepcopy(self.payload)
        old_payload.pop("file_duration_minutes")
        main.CONFIG_PATH.parent.mkdir(parents=True)
        main.CONFIG_PATH.write_text(json.dumps(old_payload))

        loaded = main.read_config()

        self.assertEqual(
            loaded["file_duration_minutes"],
            main.DEFAULT_FILE_DURATION_MINUTES,
        )

    def test_sensor_config_save_preserves_the_file_duration(self):
        main.write_config(copy.deepcopy(self.payload))
        sensor_payload = copy.deepcopy(self.payload)
        sensor_payload.pop("file_duration_minutes")

        response = main.save_config(sensor_payload)

        self.assertEqual(response, {"status": "success"})
        self.assertEqual(main.read_file_duration(), 25)


if __name__ == "__main__":
    unittest.main()
