import os
from pathlib import Path
import shutil
import unittest

from app.services.local_tesseract_service import extract_text_from_image
from app.services.rpl_screenshot_import_service import parse_rpl_ocr


@unittest.skipUnless(os.getenv("RUN_TESSERACT_SMOKE") == "1", "set RUN_TESSERACT_SMOKE=1 for local OCR integration")
class RplTesseractSmokeTest(unittest.TestCase):
    def test_real_screenshot(self):
        self.assertIsNotNone(shutil.which("tesseract"))
        image = Path(os.environ["RPL_TEST_SCREENSHOT"])
        matches = parse_rpl_ocr(
            extract_text_from_image(image),
            {"id": 5, "start_date": "2026-07-01", "end_date": "2027-05-31"},
        )
        self.assertEqual([(m["home_team"], m["away_team"], m["time"]) for m in matches], [
            ("Зенит", "Динамо", "14:30"),
            ("Крылья Советов", "Динамо Мх", "17:00"),
            ("Балтика", "Спартак", "19:30"),
        ])
