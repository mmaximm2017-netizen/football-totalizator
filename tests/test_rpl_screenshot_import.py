import io
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from PIL import Image
from werkzeug.datastructures import FileStorage

from app.services.local_tesseract_service import (
    OcrError, OcrLine, OcrResult, OcrTimeoutError, OcrWord,
    extract_text_from_image,
)
from app.services.rpl_screenshot_import_service import (
    _candidate_team,
    ImageValidationError,
    draft_is_valid,
    make_draft,
    mark_preview_duplicates,
    parse_rpl_ocr,
    resolve_match_date,
    run_import,
    save_validated_upload,
    validate_confirmed_fields,
)
from app.services.rpl_team_catalog import match_rpl_team


ROOT = Path(__file__).resolve().parents[1]
TOURNAMENT = {"id": 5, "start_date": "2026-07-01", "end_date": "2027-05-31"}


def _single_team_candidate(value):
    return _candidate_team(value)


def image_upload(fmt="JPEG", mime=None, size=(32, 32)):
    stream = io.BytesIO()
    Image.new("RGB", size, "white").save(stream, fmt)
    stream.seek(0)
    return FileStorage(
        stream=stream, filename=f"screen.{fmt.lower()}",
        content_type=mime or {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}[fmt],
    )


def spatial_result(rows, *, width=1200, height=None):
    """Build word-level TSV-like geometry: date | two team rows | time."""
    words = []
    word_number = 0

    def add(text, left, top, line_num):
        nonlocal word_number
        word_number += 1
        words.append(OcrWord(
            text, left, top, max(18, len(text) * 14), 28, 90.0,
            1, 1, 1, line_num, word_number,
        ))

    for index, row in enumerate(rows):
        base = 40 + index * 170
        line = index * 3 + 1
        if row.get("date", "10.10.") is not None:
            add(row.get("date", "10.10."), 40, base + 28, line)
        for offset, token in enumerate(row.get("home", "").split()):
            add(token, 260 + offset * 155, base, line)
        for offset, token in enumerate(row.get("away", "").split()):
            add(token, 260 + offset * 155, base + 65, line + 1)
        for extra in row.get("extras", ()):
            text, left, top_offset, line_offset = extra
            add(text, left, base + top_offset, line + line_offset)
        if row.get("time", "18:00") is not None:
            add(row.get("time", "18:00"), 1010, base + 28, line)
    words.sort(key=lambda word: (word.top, word.left))
    lines = tuple(
        OcrLine(word.text, word.top, word.left, word.width, word.height, word.confidence)
        for word in words
    )
    return OcrResult(
        "\n".join(word.text for word in words), lines, tuple(words),
        width, height or max(200, len(rows) * 170 + 40),
    )


class Cursor:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.executed = []
        self.rowcount = 1

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None


class Connection:
    def __init__(self, cursor):
        self.cursor_value = cursor
        self.commits = 0
        self.rollbacks = 0

    def cursor(self): return self.cursor_value
    def commit(self): self.commits += 1
    def rollback(self): self.rollbacks += 1


class TransactionCursor:
    """Small transaction-faithful DB double for route/service integration tests."""
    def __init__(self):
        self.executed = []
        self.pending = []
        self.committed = []
        self._result = None

    def execute(self, query, params=None):
        self.executed.append((query, params))
        normalized = " ".join(query.split())
        if "FROM tournaments" in normalized:
            self._result = (5, "Чемпионат России 🇷🇺", 1, "2026-07-01")
        elif normalized.startswith("SELECT id FROM matches"):
            key = (params[2], params[3], params[4])
            rows = self.committed + self.pending
            self._result = (rows.index(key) + 1,) if key in rows else None
        elif normalized.startswith("INSERT INTO matches"):
            self.pending.append((params[0], params[1], params[2]))
            self._result = (len(self.committed) + len(self.pending),)
        else:
            self._result = None

    def fetchone(self):
        result, self._result = self._result, None
        return result


class TransactionConnection(Connection):
    def commit(self):
        self.cursor_value.committed.extend(self.cursor_value.pending)
        self.cursor_value.pending.clear()
        super().commit()

    def rollback(self):
        self.cursor_value.pending.clear()
        super().rollback()


class RplImageValidationTests(unittest.TestCase):
    def test_jpeg_png_and_webp_are_accepted_by_real_format(self):
        for fmt in ("JPEG", "PNG", "WEBP"):
            with self.subTest(fmt=fmt):
                path = save_validated_upload(image_upload(fmt))
                try:
                    self.assertTrue(os.path.isfile(path))
                finally:
                    os.unlink(path)

    def test_invalid_image_is_rejected(self):
        upload = FileStorage(io.BytesIO(b"not-image"), filename="x.jpg", content_type="image/jpeg")
        with self.assertRaises(ImageValidationError):
            save_validated_upload(upload)

    def test_fake_content_type_is_rejected(self):
        with self.assertRaises(ImageValidationError):
            save_validated_upload(image_upload("JPEG", "application/pdf"))

    def test_oversized_file_is_rejected(self):
        import app.services.rpl_screenshot_import_service as service
        upload = image_upload("JPEG")
        with patch.object(service, "MAX_IMAGE_BYTES", 4):
            with self.assertRaises(ImageValidationError):
                save_validated_upload(upload)

    def test_excessive_resolution_is_rejected(self):
        import app.services.rpl_screenshot_import_service as service
        with patch.object(service, "MAX_IMAGE_PIXELS", 100):
            with self.assertRaises(ImageValidationError):
                save_validated_upload(image_upload("PNG", size=(20, 20)))

    def test_temporary_file_is_removed_after_ocr_error(self):
        captured = []
        def fail(path):
            captured.append(path)
            raise OcrError("fail")
        with patch("app.services.rpl_screenshot_import_service.extract_text_from_image", side_effect=fail):
            with self.assertRaises(OcrError):
                run_import(image_upload(), TOURNAMENT, 7)
        self.assertFalse(os.path.exists(captured[0]))

    def test_image_without_matches_is_rejected_and_temp_removed(self):
        captured = []
        empty = OcrResult("обычный текст", (OcrLine("обычный текст", 1, 1, 10, 10),))
        def return_empty(path):
            captured.append(path)
            return empty
        with patch("app.services.rpl_screenshot_import_service.extract_text_from_image", side_effect=return_empty):
            with self.assertRaises(ImageValidationError):
                run_import(image_upload(), TOURNAMENT, 7)
        self.assertFalse(os.path.exists(captured[0]))

    def test_tesseract_adapter_uses_fixed_safe_arguments_and_tsv(self):
        tsv = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n5\t1\t1\t1\t1\t1\t0\t0\t10\t10\t90\tЗенит\n"
        with patch("app.services.local_tesseract_service.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = tsv
            result = extract_text_from_image("/tmp/safe.jpg")
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["tesseract", "/tmp/safe.jpg", "stdout", "-l", "rus+eng", "--psm", "6", "tsv"])
        self.assertFalse(kwargs["shell"])
        self.assertEqual(result.raw_text, "Зенит")

    def test_tesseract_timeout_and_process_error_are_wrapped(self):
        import subprocess
        with patch("app.services.local_tesseract_service.subprocess.run", side_effect=subprocess.TimeoutExpired("tesseract", 1)):
            with self.assertRaises(OcrTimeoutError):
                extract_text_from_image("x", timeout=1)
        with patch("app.services.local_tesseract_service.subprocess.run") as run:
            run.return_value.returncode = 1
            run.return_value.stdout = ""
            with self.assertRaises(OcrError):
                extract_text_from_image("x")


class RplParserTests(unittest.TestCase):
    def real_like_result(self):
        texts = [
            "08:23 H 23", "Воскресенье 16.08.", "Премьер-лига", "РОССИЯ",
            "Зенит Е)", "14:30 4.20", "Динамо Москва 5.38",
            "Крылья Советов ao", "17:00 3.20", "Ф Динамо Махачкала 3.18",
            "Балтика 4.26", "19:30 3.53", "Спартак Москва 2.01", "2— Таблица »",
        ]
        lines = tuple(OcrLine(text, index * 30, 0, 300, 25, 75.0) for index, text in enumerate(texts))
        return OcrResult("\n".join(texts), lines)

    def test_tesseract_like_output_becomes_three_matches_and_ignores_odds(self):
        matches = parse_rpl_ocr(self.real_like_result(), TOURNAMENT)
        self.assertEqual([(m["home_team"], m["away_team"], m["time"]) for m in matches], [
            ("Зенит", "Динамо", "14:30"),
            ("Крылья Советов", "Динамо Мх", "17:00"),
            ("Балтика", "Спартак", "19:30"),
        ])
        self.assertTrue(all(m["date"] == "2026-08-16" for m in matches))
        self.assertNotIn("04:20", [m["time"] for m in matches])

    def test_known_aliases_are_canonical_and_dynamo_clubs_stay_distinct(self):
        self.assertEqual(match_rpl_team("Спартак Москва")[0], "Спартак")
        self.assertEqual(match_rpl_team("ФК Динамо Москва")[0], "Динамо")
        self.assertEqual(match_rpl_team("Динамо Махачкала")[0], "Динамо Мх")
        self.assertEqual(match_rpl_team("Родина Москва")[0], "Родина")
        self.assertEqual(match_rpl_team("Факел Воронеж")[0], "Факел")

    def test_ocr_edge_noise_is_narrow_and_not_fuzzy_matching(self):
        accepted = {
            "je Зенит": "Зенит",
            "Крылья Советов AEH": "Крылья Советов",
            "и Балтика OZ": "Балтика",
            "Зенит Е": "Зенит",
            "Крылья Советов ao": "Крылья Советов",
            "Ф Динамо Махачкала": "Динамо Мх",
        }
        for value, canonical in accepted.items():
            with self.subTest(value=value):
                self.assertEqual(_single_team_candidate(value)["canonical"], canonical)

        rejected = (
            "Зенитт", "Зенит X", "Зенит Краснодар", "Спартак Динамо",
            "Краснодар Зенит", "Динамо Балтика", "Зен", "Зенитовец",
            "СуперЗенит", "Неизвестный Зенитоград",
        )
        for value in rejected:
            with self.subTest(value=value):
                candidate = _single_team_candidate(value)
                self.assertIsNotNone(candidate)
                self.assertEqual(candidate["canonical"], "")
                self.assertEqual(candidate["status"], "needs_review")

    def test_production_tesseract_output_normalizes_all_three_matches(self):
        texts = [
            "Воскресенье 16.08.", "Премьер-лига", "РОССИЯ",
            "je Зенит", "14:30", "Динамо Москва",
            "Крылья Советов AEH", "17:00", "Динамо Махачкала",
            "и Балтика OZ", "19:30", "Спартак Москва", "Таблица",
        ]
        result = OcrResult(
            "\n".join(texts),
            tuple(OcrLine(text, index * 30, 0, 300, 25, 80.0) for index, text in enumerate(texts)),
        )
        diagnostics = {}
        matches = parse_rpl_ocr(result, TOURNAMENT, diagnostics)
        self.assertEqual([
            (match["home_team"], match["away_team"], match["date"], match["time"], match["status"])
            for match in matches
        ], [
            ("Зенит", "Динамо", "2026-08-16", "14:30", "ready"),
            ("Крылья Советов", "Динамо Мх", "2026-08-16", "17:00", "ready"),
            ("Балтика", "Спартак", "2026-08-16", "19:30", "ready"),
        ])
        self.assertEqual(diagnostics["parser_mode"], "flat")

    def test_spatial_three_column_layout_builds_eight_ordered_match_regions(self):
        rows = [
            {"home": "Крылья Советов", "away": "Спартак Москва"},
            {"home": "Ростов", "away": "Акрон Тольятти"},
            {"home": "Оренбург", "away": "Динамо Махачкала"},
            {"home": "Краснодар", "away": "Зенит"},
            {"home": "Ахмат", "away": "Балтика"},
            {"home": "ЦСКА", "away": "Родина"},
            {"home": "Рубин", "away": "Динамо Москва"},
            {"home": "Факел", "away": "Локомотив Москва"},
        ]
        diagnostics = {}
        matches = parse_rpl_ocr(spatial_result(rows), TOURNAMENT, diagnostics)
        self.assertEqual(len(matches), 8)
        self.assertEqual([(m["home_team"], m["away_team"]) for m in matches], [
            ("Крылья Советов", "Спартак"), ("Ростов", "Акрон"),
            ("Оренбург", "Динамо Мх"), ("Краснодар", "Зенит"),
            ("Ахмат", "Балтика"), ("ЦСКА", "Родина"),
            ("Рубин", "Динамо"), ("Факел", "Локомотив"),
        ])
        self.assertTrue(all(m["date"] == "2026-10-10" and m["time"] == "18:00" for m in matches))
        self.assertTrue(all(m["status"] == "ready" for m in matches))
        self.assertEqual(matches[6]["status"], "ready")
        self.assertEqual(diagnostics, {
            "time_anchors": 8, "match_regions": 8, "complete_matches": 8,
            "review_regions": 0, "parser_mode": "spatial",
        })

    def test_spatial_incomplete_and_ambiguous_regions_require_review(self):
        cases = {
            "one team": [{"home": "Зенит", "away": ""}],
            "three teams": [{
                "home": "Зенит", "away": "Динамо Москва",
                "extras": (("Балтика", 260, 100, 2),),
            }],
            "two teams one line": [{
                "home": "Спартак Динамо", "away": "",
            }],
            "missing date": [{"home": "Зенит", "away": "Динамо Москва", "date": None}],
            "invalid date": [{"home": "Зенит", "away": "Динамо Москва", "date": "31.02."}],
        }
        for name, rows in cases.items():
            with self.subTest(name=name):
                matches = parse_rpl_ocr(spatial_result(rows), TOURNAMENT)
                self.assertEqual(len(matches), 1)
                self.assertEqual(matches[0]["status"], "needs_review")

    def test_spatial_repeated_times_noise_and_region_boundaries_are_safe(self):
        rows = [
            {
                "home": "je Зенит", "away": "Динамо Москва",
                "extras": (("X", 210, 48, 2),),
            },
            {"home": "Крылья Советов AEH", "away": "Спартак Москва"},
            {"home": "Ахмат", "away": "Балтика"},
        ]
        result = spatial_result(rows)
        matches = parse_rpl_ocr(result, TOURNAMENT)
        self.assertEqual([(m["home_team"], m["away_team"], m["time"]) for m in matches], [
            ("Зенит", "Динамо", "18:00"),
            ("Крылья Советов", "Спартак", "18:00"),
            ("Ахмат", "Балтика", "18:00"),
        ])
        self.assertTrue(all(match["status"] == "ready" for match in matches))

    def test_spatial_date_inheritance_requires_equal_neighbours(self):
        rows = [
            {"home": "Зенит", "away": "Динамо Москва"},
            {"home": "Ахмат", "away": "Балтика", "date": None},
            {"home": "Рубин", "away": "Спартак Москва"},
        ]
        matches = parse_rpl_ocr(spatial_result(rows), TOURNAMENT)
        self.assertTrue(all(match["date"] == "2026-10-10" for match in matches))
        self.assertTrue(all(match["status"] == "ready" for match in matches))

        rows[2]["date"] = "11.10."
        matches = parse_rpl_ocr(spatial_result(rows), TOURNAMENT)
        self.assertEqual(matches[1]["status"], "needs_review")
        self.assertEqual(matches[1]["date"], "")

    def test_unrelated_clock_column_does_not_create_a_match_region(self):
        result = spatial_result([
            {"home": "Зенит", "away": "Динамо Москва"},
            {"home": "Ахмат", "away": "Балтика"},
        ])
        status_clock = OcrWord("08:23", 20, 1, 70, 25, 95.0, 1, 9, 1, 1, 1)
        result = OcrResult(
            result.raw_text, result.lines, result.words + (status_clock,),
            result.image_width, result.image_height,
        )
        diagnostics = {}
        matches = parse_rpl_ocr(result, TOURNAMENT, diagnostics)
        self.assertEqual(len(matches), 2)
        self.assertEqual(diagnostics["time_anchors"], 2)

    def test_extra_aligned_time_anchor_creates_review_region_without_reusing_teams(self):
        result = spatial_result([
            {"home": "Зенит", "away": "Динамо Москва"},
            {"home": "Ахмат", "away": "Балтика"},
        ])
        extra_time = OcrWord("18:00", 1010, 153, 70, 28, 90.0, 1, 1, 1, 99, 99)
        result = OcrResult(
            result.raw_text, result.lines, result.words + (extra_time,),
            result.image_width, result.image_height,
        )
        matches = parse_rpl_ocr(result, TOURNAMENT)
        self.assertEqual(len(matches), 3)
        self.assertEqual(sum(match["status"] == "needs_review" for match in matches), 1)
        teams = [team for match in matches for team in (match["home_team"], match["away_team"]) if team]
        self.assertEqual(teams.count("Зенит"), 1)
        self.assertEqual(teams.count("Балтика"), 1)

    def test_team_tokens_are_not_joined_across_region_boundary(self):
        result = spatial_result([
            {"home": "Крылья", "away": "Зенит"},
            {"home": "Советов", "away": "Динамо Москва"},
        ])
        matches = parse_rpl_ocr(result, TOURNAMENT)
        self.assertEqual(len(matches), 2)
        self.assertTrue(all(match["status"] == "needs_review" for match in matches))
        self.assertNotIn("Крылья Советов", [
            team for match in matches for team in (match["home_team"], match["away_team"])
        ])

    def test_safe_normalization_handles_case_spaces_yo_and_hyphen(self):
        self.assertEqual(match_rpl_team("  фк   СПАРТАК-МОСКВА ")[0], "Спартак")

    def test_unknown_team_requires_review(self):
        checked = validate_confirmed_fields({"home_team": "Неизвестные", "away_team": "Зенит", "date": "2026-08-16", "time": "14:30"})
        self.assertEqual(checked["status"], "invalid")
        self.assertIn("каталог", checked["reasons"][0])

    def test_missing_or_invalid_time_is_invalid(self):
        for value in ("", "25:00", "1.72"):
            checked = validate_confirmed_fields({"home_team": "Зенит", "away_team": "Динамо", "date": "2026-08-16", "time": value})
            self.assertEqual(checked["status"], "invalid")

    def test_invalid_date_and_same_team_are_invalid(self):
        checked = validate_confirmed_fields({"home_team": "Зенит", "away_team": "Зенит", "date": "16.08", "time": "14:30"})
        self.assertEqual(checked["status"], "invalid")
        self.assertEqual(len(checked["reasons"]), 2)

    def test_year_is_deterministic_from_season_or_left_empty(self):
        self.assertEqual(resolve_match_date(16, 8, TOURNAMENT), "2026-08-16")
        self.assertEqual(resolve_match_date(16, 8, {"start_date": None, "end_date": None}), "")

    def test_missing_time_marks_parsed_match_for_review(self):
        result = OcrResult("16.08\nЗенит\nДинамо\nТаблица", (
            OcrLine("16.08", 1, 0, 1, 1), OcrLine("Зенит", 2, 0, 1, 1),
            OcrLine("Динамо", 3, 0, 1, 1), OcrLine("Таблица", 4, 0, 1, 1),
        ))
        self.assertEqual(parse_rpl_ocr(result, TOURNAMENT)[0]["status"], "needs_review")

    def test_early_kickoff_is_visible_in_preview_and_invalid_on_confirm(self):
        result = OcrResult("16.08\nЗенит\n10:30\nДинамо\nТаблица", (
            OcrLine("16.08", 1, 0, 1, 1), OcrLine("Зенит", 2, 0, 1, 1),
            OcrLine("10:30", 3, 0, 1, 1), OcrLine("Динамо", 4, 0, 1, 1),
            OcrLine("Таблица", 5, 0, 1, 1),
        ))
        parsed = parse_rpl_ocr(result, TOURNAMENT)[0]
        self.assertEqual(parsed["status"], "needs_review")
        self.assertIn("ручного дедлайна", parsed["reasons"][0])
        checked = validate_confirmed_fields(parsed)
        self.assertEqual(checked["status"], "invalid")
        self.assertIn("ручного дедлайна", checked["reasons"][0])

    def test_duplicate_in_draft_and_database_is_invalid(self):
        item = {"home_team": "Зенит", "away_team": "Динамо", "date": "2026-08-16", "time": "14:30", "reasons": []}
        draft = {"matches": [dict(item), dict(item)]}
        cursor = Cursor([None])
        mark_preview_duplicates(cursor, draft, 5)
        self.assertEqual(draft["matches"][1]["status"], "invalid")
        draft = {"matches": [dict(item)]}
        mark_preview_duplicates(Cursor([(9,)]), draft, 5)
        self.assertEqual(draft["matches"][0]["status"], "invalid")

    def test_draft_is_bound_to_user_tournament_and_ttl(self):
        draft = make_draft(7, 5, [], "")
        self.assertTrue(draft_is_valid(draft, 7, 5))
        self.assertFalse(draft_is_valid(draft, 8, 5))
        draft["created_at"] = int(time.time()) - 9999
        self.assertFalse(draft_is_valid(draft, 7, 5))

    def test_realistic_draft_cookie_size_and_contents(self):
        app = Flask(__name__)
        app.secret_key = "cookie-size-test"

        def build(count):
            matches = [{
                "raw_home_team": f"Очень длинное OCR название домашней команды {index}",
                "raw_away_team": f"Очень длинное OCR название гостевой команды {index}",
                "home_team": "Крылья Советов", "away_team": "Динамо Мх",
                "date": "2026-08-16", "display_date": "16.08", "time": "19:30",
                "status": "needs_review", "reasons": ["Требуется проверить длинное название команды"],
            } for index in range(count)]
            return make_draft(7, 5, matches, "RAW OCR AND TSV MUST NOT BE STORED")

        sizes = {}
        for count in (8, 10):
            draft = build(count)
            self.assertEqual(set(draft), {"id", "user_id", "tournament_id", "created_at", "importer_key", "league", "matches"})
            self.assertEqual(draft["importer_key"], "rpl")
            self.assertEqual(draft["league"], "rpl")
            self.assertNotIn("raw_text", draft)
            self.assertNotIn("image", draft)
            serializer = app.session_interface.get_signing_serializer(app)
            sizes[count] = len(serializer.dumps({"rpl_screenshot_draft": draft}).encode("utf-8"))
        self.assertLess(sizes[8], 4093)
        self.assertLess(sizes[10], 4093)


class RplImportRouteTests(unittest.TestCase):
    def make_app(self):
        from app.routes.admin_matches import admin_matches_bp
        app = Flask(__name__, template_folder=str(ROOT / "templates"), static_folder=str(ROOT / "static"))
        app.secret_key = "test"
        app.register_blueprint(admin_matches_bp)
        app.add_url_rule("/admin/russia-2027", "admin.admin_russia_2027", lambda: "admin")
        app.add_url_rule("/login", "auth.login", lambda: "login")
        app.add_url_rule("/", "main.index", lambda: "index")
        return app

    def route_patches(self, route_conn, admin=True):
        user_conn = Connection(Cursor([((1 if admin else 0), 0)]))
        return (
            patch("app.routes.admin_common.get_db", return_value=user_conn),
            patch("app.routes.admin_common.close_db"),
            patch("app.routes.admin_matches.get_db", return_value=route_conn),
            patch("app.routes.admin_matches.close_db"),
        )

    def test_non_admin_cannot_upload(self):
        app = self.make_app()
        with app.test_client() as client:
            with client.session_transaction() as sess: sess["user_id"] = 3
            patches = self.route_patches(Connection(Cursor()), admin=False)
            with patches[0], patches[1], patches[2], patches[3]:
                response = client.post("/admin/russia-2027/import-screenshot")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")

    def test_preview_markup_is_embedded_editable_and_removable(self):
        template = (ROOT / "templates" / "admin_russia_2027.html").read_text(encoding="utf-8")
        self.assertIn("data-rpl-import-preview", template)
        for field in ('name="home_team"', 'name="away_team"', 'name="match_date"', 'name="match_time"'):
            self.assertIn(field, template)
        self.assertIn("data-remove-row", template)
        self.assertIn("Добавить матчи", template)

    def test_preview_upload_does_not_insert_matches(self):
        app = self.make_app()
        route_cursor = Cursor([(5, "Чемпионат России 🇷🇺", 1, "2026-07-01"), None])
        route_conn = Connection(route_cursor)
        draft = make_draft(1, 5, [{"home_team": "Зенит", "away_team": "Динамо", "date": "2026-08-16", "time": "14:30", "reasons": []}], "raw")
        with app.test_client() as client:
            with client.session_transaction() as sess: sess["user_id"] = 1
            patches = self.route_patches(route_conn)
            with patches[0], patches[1], patches[2], patches[3], patch("app.routes.admin_matches.run_import", return_value=draft):
                response = client.post("/admin/russia-2027/import-screenshot", data={"screenshot": (io.BytesIO(b"x"), "x.jpg")})
                with client.session_transaction() as sess: stored = sess.get("rpl_screenshot_draft")
        self.assertEqual(response.status_code, 302)
        self.assertIsNotNone(stored)
        self.assertFalse(any("INSERT INTO matches" in sql for sql, _ in route_cursor.executed))
        self.assertEqual(route_conn.commits, 0)

    def test_ocr_error_is_handled_without_500(self):
        app = self.make_app()
        conn = Connection(Cursor([(5, "Чемпионат России 🇷🇺", 1, "2026-07-01")]))
        with app.test_client() as client:
            with client.session_transaction() as sess: sess["user_id"] = 1
            patches = self.route_patches(conn)
            with patches[0], patches[1], patches[2], patches[3], patch("app.routes.admin_matches.run_import", side_effect=OcrTimeoutError("timeout")):
                response = client.post("/admin/russia-2027/import-screenshot")
        self.assertEqual(response.status_code, 302)

    def test_confirm_revalidates_and_creates_all(self):
        app = self.make_app()
        draft = make_draft(1, 5, [{"home_team": "bad"}], "")
        cursor = Cursor([(5, "Чемпионат России 🇷🇺", 1, "2026-07-01"), None, (41,), None, (42,)])
        conn = Connection(cursor)
        payload = {
            "draft_id": draft["id"],
            "home_team": ["Зенит", "Балтика"], "away_team": ["Динамо", "Спартак"],
            "match_date": ["2026-08-16", "2026-08-16"], "match_time": ["14:30", "19:30"],
        }
        with app.test_client() as client:
            with client.session_transaction() as sess: sess.update(user_id=1, rpl_screenshot_draft=draft)
            patches = self.route_patches(conn)
            with patches[0], patches[1], patches[2], patches[3]:
                response = client.post("/admin/russia-2027/import-confirm", data=payload)
                with client.session_transaction() as sess: stored = sess.get("rpl_screenshot_draft")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(conn.commits, 1)
        self.assertEqual(sum("INSERT INTO matches" in sql for sql, _ in cursor.executed), 2)
        self.assertIsNone(stored)

    def test_invalid_confirm_rolls_back_without_insert(self):
        app = self.make_app()
        draft = make_draft(1, 5, [], "")
        cursor = Cursor([(5, "Чемпионат России 🇷🇺", 1, "2026-07-01")])
        conn = Connection(cursor)
        with app.test_client() as client:
            with client.session_transaction() as sess: sess.update(user_id=1, rpl_screenshot_draft=draft)
            patches = self.route_patches(conn)
            with patches[0], patches[1], patches[2], patches[3]:
                client.post("/admin/russia-2027/import-confirm", data={
                    "draft_id": draft["id"], "home_team": "Неизвестные", "away_team": "Зенит",
                    "match_date": "2026-08-16", "match_time": "14:30",
                })
        self.assertEqual(conn.rollbacks, 1)
        self.assertFalse(any("INSERT INTO matches" in sql for sql, _ in cursor.executed))

    def test_one_match_failure_rolls_back_entire_batch(self):
        app = self.make_app()
        draft = make_draft(1, 5, [], "")
        conn = Connection(Cursor([(5, "Чемпионат России 🇷🇺", 1, "2026-07-01")]))
        payload = {"draft_id": draft["id"], "home_team": ["Зенит", "Балтика"], "away_team": ["Динамо", "Спартак"], "match_date": ["2026-08-16"] * 2, "match_time": ["14:30", "19:30"]}
        with app.test_client() as client:
            with client.session_transaction() as sess: sess.update(user_id=1, rpl_screenshot_draft=draft)
            patches = self.route_patches(conn)
            with patches[0], patches[1], patches[2], patches[3], patch("app.routes.admin_matches.create_manual_match", side_effect=[41, OcrError("db fail")]):
                client.post("/admin/russia-2027/import-confirm", data=payload)
        self.assertEqual(conn.commits, 0)
        self.assertEqual(conn.rollbacks, 1)

    def test_three_real_service_calls_third_duplicate_rolls_back_pending_inserts(self):
        app = self.make_app()
        draft = make_draft(1, 5, [], "")
        cursor = TransactionCursor()
        conn = TransactionConnection(cursor)
        payload = {
            "draft_id": draft["id"],
            "home_team": ["Зенит", "Балтика", "Зенит"],
            "away_team": ["Динамо", "Спартак", "Динамо"],
            "match_date": ["2026-08-16"] * 3,
            "match_time": ["14:30", "19:30", "14:30"],
        }
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess.update(user_id=1, rpl_screenshot_draft=draft)
            patches = self.route_patches(conn)
            with patches[0], patches[1], patches[2], patches[3]:
                client.post("/admin/russia-2027/import-confirm", data=payload)
        self.assertEqual(sum("INSERT INTO matches" in sql for sql, _ in cursor.executed), 2)
        self.assertEqual(conn.commits, 0)
        self.assertEqual(conn.rollbacks, 1)
        self.assertEqual(cursor.pending, [])
        self.assertEqual(cursor.committed, [])

    def test_repeated_confirm_cannot_create_again(self):
        app = self.make_app()
        expired = make_draft(1, 5, [], "")
        expired["created_at"] = 0
        conn = Connection(Cursor([(5, "Чемпионат России 🇷🇺", 1, "2026-07-01")]))
        with app.test_client() as client:
            with client.session_transaction() as sess: sess.update(user_id=1, rpl_screenshot_draft=expired)
            patches = self.route_patches(conn)
            with patches[0], patches[1], patches[2], patches[3], patch("app.routes.admin_matches.create_manual_match") as create:
                client.post("/admin/russia-2027/import-confirm", data={"draft_id": expired["id"]})
        create.assert_not_called()
        self.assertEqual(conn.rollbacks, 1)


if __name__ == "__main__":
    unittest.main()
