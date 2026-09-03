from app.services.local_tesseract_service import extract_text_from_image
from app.services.russian_cup_team_catalog import (
    RUSSIAN_CUP_ALIAS_TOKENS, RUSSIAN_CUP_TEAM_ALIASES, match_russian_cup_team,
)
from app.services.screenshot_match_import_service import (
    ImportConfig, ImageValidationError, make_generic_draft, parse_ocr,
    save_validated_upload,
)
import os

IMPORTER_KEY = "russian_cup"
LEAGUE = "rcup"
MATCH_CATEGORY = "russian_cup"
CUP_IMPORT_CONFIG = ImportConfig(IMPORTER_KEY, LEAGUE, MATCH_CATEGORY,
                                  RUSSIAN_CUP_TEAM_ALIASES, match_russian_cup_team,
                                  RUSSIAN_CUP_ALIAS_TOKENS)

def run_import(upload, tournament, user_id):
    path = save_validated_upload(upload)
    try:
        result = extract_text_from_image(path)
        matches = parse_ocr(result, tournament, CUP_IMPORT_CONFIG)
        if not matches:
            raise ImageValidationError("На изображении не найдено расписание матчей Кубка России")
        return make_generic_draft(user_id, tournament["id"], matches, IMPORTER_KEY, LEAGUE, result.raw_text)
    finally:
        try: os.unlink(path)
        except FileNotFoundError: pass
