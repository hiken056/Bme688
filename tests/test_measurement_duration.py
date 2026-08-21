import json
from pathlib import Path
import tempfile
from unittest import TestCase
from unittest.mock import MagicMock, mock_open, patch

from pydantic import ValidationError

from backend import main


class FileRotationTests(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_config_path = main.CONFIG_PATH
        self.original_legacy_path = main.LEGACY_CONFIG_PATH
        self.original_sensor_dir = main.SENSOR_DIR
        self.original_sensor_binary = main.SENSOR_BINARY
        self.original_measurements_dir = main.MEASUREMENTS_DIR
        root = Path(self.temp_dir.name)
        main.CONFIG_PATH = root / "var" / "lib" / "bme688" / "config.json"
        main.LEGACY_CONFIG_PATH = root / "tmp" / "bme_config.json"
        main.SENSOR_DIR = root / "bme-code"
        main.SENSOR_BINARY = main.SENSOR_DIR / "main"
        main.MEASUREMENTS_DIR = main.SENSOR_DIR / "measurements"
        main.sensor_process = None
        main.sensor_file_started_at = None
        main.sensor_file_duration_minutes = None
        main.sensor_measurement_title = None

    def tearDown(self):
        main.CONFIG_PATH = self.original_config_path
        main.LEGACY_CONFIG_PATH = self.original_legacy_path
        main.SENSOR_DIR = self.original_sensor_dir
        main.SENSOR_BINARY = self.original_sensor_binary
        main.MEASUREMENTS_DIR = self.original_measurements_dir
        self.temp_dir.cleanup()
        main.sensor_process = None
        main.sensor_file_started_at = None
        main.sensor_file_duration_minutes = None
        main.sensor_measurement_title = None

    def test_duration_is_limited_to_dropdown_values(self):
        with self.assertRaises(ValidationError):
            main.FileDurationRequest(file_duration_minutes=10)

    def test_selected_duration_is_saved_and_loaded(self):
        response = main.save_file_duration(
            main.FileDurationRequest(file_duration_minutes=20)
        )

        self.assertEqual(response["status"], "success")
        self.assertEqual(main.get_file_duration()["file_duration_minutes"], 20)
        self.assertEqual(json.loads(main.CONFIG_PATH.read_text())["file_duration_minutes"], 20)

    @patch("backend.main.sensor_program_supports_measurement_folders", return_value=True)
    @patch("backend.main.time.monotonic", return_value=100.0)
    @patch("builtins.open", new_callable=mock_open)
    @patch("backend.main.subprocess.Popen")
    def test_selected_file_duration_is_passed_to_sensor_process(
        self, popen, _open_file, _monotonic, _supports_measurement_folders
    ):
        process = MagicMock()
        process.pid = 42
        process.poll.return_value = None
        popen.return_value = process

        response = main.start_sensor(main.StartSensorRequest(
            measurement_title="Ethanol test",
            file_duration_minutes=25,
        ))

        self.assertEqual(response["file_duration_minutes"], 25)
        self.assertEqual(response["measurement_title"], "Ethanol-test")
        popen.assert_called_once()
        measurement_dir = main.SENSOR_DIR / response["measurement_path"]
        self.assertEqual(
            popen.call_args.args[0],
            ["./main", "25"],
        )
        self.assertEqual(
            popen.call_args.kwargs["env"]["BME_MEASUREMENT_DIR"],
            str(measurement_dir),
        )
        self.assertTrue(measurement_dir.is_dir())
        self.assertEqual(main.sensor_file_started_at, 100.0)

    @patch("backend.main.sensor_program_supports_measurement_folders", return_value=True)
    @patch("backend.main.time.monotonic", return_value=100.0)
    @patch("builtins.open", new_callable=mock_open)
    @patch("backend.main.subprocess.Popen")
    def test_start_without_a_selection_uses_the_saved_duration(
        self, popen, _open_file, _monotonic, _supports_measurement_folders
    ):
        process = MagicMock()
        process.pid = 42
        process.poll.return_value = None
        popen.return_value = process
        main.write_file_duration(20)

        response = main.start_sensor(main.StartSensorRequest(
            measurement_title="Field test"
        ))

        self.assertEqual(response["file_duration_minutes"], 20)
        self.assertEqual(popen.call_args.args[0][0:2], ["./main", "20"])

    @patch("backend.main.sensor_program_supports_measurement_folders", return_value=False)
    def test_outdated_sensor_program_is_rejected(self, _supports_measurement_folders):
        response = main.start_sensor(main.StartSensorRequest(
            measurement_title="Field test"
        ))

        self.assertEqual(response["status"], "error")
        self.assertIn("outdated", response["message"])
        self.assertFalse(main.MEASUREMENTS_DIR.exists())

    def test_reused_title_uses_the_same_measurement_folder(self):
        title, first = main.get_measurement_folder("Ethanol test")
        _, second = main.get_measurement_folder("Ethanol test")

        self.assertEqual(title, "Ethanol-test")
        self.assertEqual(first, main.MEASUREMENTS_DIR / "Ethanol-test")
        self.assertEqual(second, first)

    def test_title_cannot_escape_the_measurements_folder(self):
        title = main.safe_measurement_title("../../field / test")

        self.assertEqual(title, "field-test")
        self.assertNotIn("/", title)

    @patch("backend.main.time.monotonic", return_value=160.0)
    def test_status_reports_time_until_next_file(self, _monotonic):
        process = MagicMock()
        process.poll.return_value = None
        main.sensor_process = process
        main.sensor_file_started_at = 100.0
        main.sensor_file_duration_minutes = 15

        response = main.get_status()

        self.assertTrue(response["is_running"])
        self.assertEqual(response["file_duration_minutes"], 15)
        self.assertEqual(response["file_remaining_seconds"], 840)
