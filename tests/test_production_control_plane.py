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


def test_deploy_does_not_fetch_release_from_github_on_vps():
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")

    assert "git fetch --quiet origin" not in workflow
    assert 'git show "$release_sha:' not in workflow
    assert "Copy production control bundle to VPS" in workflow
    assert "production-control-plane.tar.gz" in workflow
    assert "actions/checkout@v4" in workflow


def test_deploy_verifies_bundle_release_matches_image_release():
    workflow = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")

    assert 'expected_release_sha="${{ steps.image.outputs.release_sha }}"' in workflow
    assert 'if [[ "$release_sha" != "$expected_release_sha" ]]; then' in workflow
    assert "image release does not match CI release" in workflow
