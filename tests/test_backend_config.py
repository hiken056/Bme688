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
        self.original_legacy_path = main.LEGACY_CONFIG_PATH
        main.CONFIG_PATH = root / "var" / "lib" / "bme688" / "config.json"
        main.LEGACY_CONFIG_PATH = root / "tmp" / "bme_config.json"

        self.payload = {
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
        main.LEGACY_CONFIG_PATH = self.original_legacy_path
        self.temp_dir.cleanup()

    def test_save_config_writes_durable_file_and_reads_it_back(self):
        response = main.save_config(copy.deepcopy(self.payload))

        self.assertEqual(response, {"status": "success"})
        self.assertTrue(main.CONFIG_PATH.exists())
        self.assertEqual(main.get_config(), self.payload)
        self.assertEqual(json.loads(main.CONFIG_PATH.read_text()), self.payload)
        self.assertEqual(list(main.CONFIG_PATH.parent.glob(".config.json.*")), [])

    def test_legacy_tmp_config_is_migrated_and_sleep_key_is_normalized(self):
        legacy_payload = copy.deepcopy(self.payload)
        legacy_preset = legacy_payload["presets"][0]
        legacy_preset["sleep_sec"] = legacy_preset.pop("sleep")
        main.LEGACY_CONFIG_PATH.parent.mkdir(parents=True)
        main.LEGACY_CONFIG_PATH.write_text(json.dumps(legacy_payload))

        loaded = main.read_config()

        self.assertEqual(loaded, self.payload)
        self.assertTrue(main.CONFIG_PATH.exists())
        self.assertEqual(json.loads(main.CONFIG_PATH.read_text()), self.payload)

    def test_durable_config_takes_priority_over_legacy_tmp_file(self):
        durable_payload = copy.deepcopy(self.payload)
        legacy_payload = copy.deepcopy(self.payload)
        legacy_payload["presets"][0]["name"] = "Stale legacy profile"
        main.write_config(durable_payload)
        main.LEGACY_CONFIG_PATH.parent.mkdir(parents=True)
        main.LEGACY_CONFIG_PATH.write_text(json.dumps(legacy_payload))

        self.assertEqual(main.read_config(), durable_payload)


if __name__ == "__main__":
    unittest.main()
