from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_ci_uses_production_postgres_major_version():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "image: postgres:17" in workflow
    assert "image: postgres:16" not in workflow


def test_ci_postgres_healthcheck_uses_ci_role():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert 'pg_isready -U totish_ci -d totish_ci' in workflow
