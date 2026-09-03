from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_all_production_managed_files_exist():
    manifest = ROOT / "deploy" / "production-managed-files.txt"
    paths = [
        line.strip()
        for line in manifest.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert paths
    missing = [path for path in paths if not (ROOT / path).is_file()]
    assert missing == []


def test_production_cron_contains_expected_jobs_once():
    cron = (ROOT / "deploy" / "production.cron").read_text(encoding="utf-8")

    expected = [
        "host_telegram_notifier.py",
        "monitor_production.py",
        "run_morning_digest.sh",
        "run_deadline_pushes.sh",
        "run_match_result_pushes.sh",
    ]
    for name in expected:
        assert cron.count(name) == 1


def test_production_cron_does_not_embed_secrets():
    cron = (ROOT / "deploy" / "production.cron").read_text(encoding="utf-8")

    forbidden = [
        "DATABASE_URL=",
        "SECRET_KEY=",
        "TELEGRAM_ERROR_BOT_TOKEN=",
        "TOTISH_AGENT_TOKEN=",
    ]
    for value in forbidden:
        assert value not in cron
