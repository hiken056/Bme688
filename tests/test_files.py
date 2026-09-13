from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from fastapi import HTTPException
from fastapi.responses import FileResponse

from backend import main


class MeasurementFilesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.original_measurements_dir = main.MEASUREMENTS_DIR
        main.MEASUREMENTS_DIR = Path(self.temp.name) / "measurements"

        self.run_one = main.MEASUREMENTS_DIR / "Clean-air" / "2026-09-13_16-20-04_15min"
        self.run_two = main.MEASUREMENTS_DIR / "Clean-air" / "2026-09-13_16-35-04_15min"
        self.run_one.mkdir(parents=True)
        self.run_two.mkdir(parents=True)
        (self.run_one / "sensor1_hp_301_basic.csv").write_text("one\n")
        (self.run_one / "sensor2_hp_301_basic.csv").write_text("two\n")
        (self.run_two / "sensor1_hp_301_basic.csv").write_text("three\n")

    def tearDown(self):
        main.MEASUREMENTS_DIR = self.original_measurements_dir

    @patch("backend.main.sensor_is_running", return_value=False)
    def test_files_are_grouped_by_title_and_recording(self, _running):
        response = main.get_measurement_files()

        self.assertEqual(response["status"], "success")
        self.assertEqual(response["recording_count"], 2)
        self.assertEqual(response["file_count"], 3)
        measurement = response["measurements"][0]
        self.assertEqual(measurement["name"], "Clean-air")
        self.assertEqual(measurement["display_name"], "Clean air")
        self.assertEqual(measurement["runs"][0]["name"], self.run_two.name)
        run = next(item for item in measurement["runs"] if item["name"] == self.run_one.name)
        self.assertEqual(run["duration_minutes"], 15)
        self.assertEqual(run["recorded_at"], "2026-09-13T16:20:04")
        self.assertEqual(run["files"][0]["path"],
                         "Clean-air/2026-09-13_16-20-04_15min/sensor1_hp_301_basic.csv")

    @patch("backend.main.sensor_is_running", return_value=False)
    def test_one_file_downloads_without_an_archive(self, _running):
        request = main.DownloadFilesRequest(paths=[
            "Clean-air/2026-09-13_16-20-04_15min/sensor1_hp_301_basic.csv"
        ])

        response = main.download_measurement_files(request)

        self.assertIsInstance(response, FileResponse)
        self.assertEqual(Path(response.path).resolve(),
                         (self.run_one / "sensor1_hp_301_basic.csv").resolve())
        self.assertIn("sensor1_hp_301_basic.csv", response.headers["content-disposition"])

    @patch("backend.main.sensor_is_running", return_value=False)
    def test_multiple_files_download_as_a_standard_zip(self, _running):
        paths = [
            "Clean-air/2026-09-13_16-20-04_15min/sensor1_hp_301_basic.csv",
            "Clean-air/2026-09-13_16-35-04_15min/sensor1_hp_301_basic.csv",
        ]
        response = main.download_measurement_files(main.DownloadFilesRequest(
            paths=paths,
            archive_name="Clean air",
        ))
        archive_path = Path(response.path)
        self.addCleanup(archive_path.unlink, missing_ok=True)

        self.assertEqual(response.media_type, "application/zip")
        self.assertIn("Clean-air.zip", response.headers["content-disposition"])
        with zipfile.ZipFile(archive_path) as archive:
            self.assertEqual(sorted(archive.namelist()), sorted(paths))
            self.assertEqual(archive.testzip(), None)

    @patch("backend.main.sensor_is_running", return_value=False)
    def test_download_cannot_escape_measurements_directory(self, _running):
        with self.assertRaises(HTTPException) as raised:
            main.download_measurement_files(main.DownloadFilesRequest(paths=["../config.json"]))

        self.assertEqual(raised.exception.status_code, 400)

    @patch("backend.main.sensor_is_running", return_value=False)
    def test_selected_files_are_deleted(self, _running):
        selected = self.run_one / "sensor1_hp_301_basic.csv"

        response = main.delete_measurement_files(main.DeleteFilesRequest(paths=[
            "Clean-air/2026-09-13_16-20-04_15min/sensor1_hp_301_basic.csv"
        ]))

        self.assertEqual(response["deleted_count"], 1)
        self.assertFalse(selected.exists())
        self.assertTrue(self.run_one.exists())
        self.assertTrue((self.run_one / "sensor2_hp_301_basic.csv").exists())

    @patch("backend.main.sensor_is_running", return_value=False)
    def test_empty_recording_and_title_folders_are_removed(self, _running):
        paths = [
            "Clean-air/2026-09-13_16-20-04_15min/sensor1_hp_301_basic.csv",
            "Clean-air/2026-09-13_16-20-04_15min/sensor2_hp_301_basic.csv",
            "Clean-air/2026-09-13_16-35-04_15min/sensor1_hp_301_basic.csv",
        ]

        response = main.delete_measurement_files(main.DeleteFilesRequest(paths=paths))

        self.assertEqual(response["deleted_count"], 3)
        self.assertFalse((main.MEASUREMENTS_DIR / "Clean-air").exists())

    @patch("backend.main.sensor_is_running", return_value=False)
    def test_all_paths_are_checked_before_deletion(self, _running):
        selected = self.run_one / "sensor1_hp_301_basic.csv"
        with self.assertRaises(HTTPException):
            main.delete_measurement_files(main.DeleteFilesRequest(paths=[
                "Clean-air/2026-09-13_16-20-04_15min/sensor1_hp_301_basic.csv",
                "../outside.csv",
            ]))

        self.assertTrue(selected.exists())

    @patch("backend.main.sensor_is_running", return_value=True)
    def test_listing_and_downloads_are_blocked_while_measuring(self, _running):
        with self.assertRaises(HTTPException) as listing:
            main.get_measurement_files()
        with self.assertRaises(HTTPException) as download:
            main.download_measurement_files(main.DownloadFilesRequest(paths=["anything.csv"]))
        with self.assertRaises(HTTPException) as delete:
            main.delete_measurement_files(main.DeleteFilesRequest(paths=["anything.csv"]))

        self.assertEqual(listing.exception.status_code, 409)
        self.assertEqual(download.exception.status_code, 409)
        self.assertEqual(delete.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
