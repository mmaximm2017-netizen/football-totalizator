from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_deploy_reads_previous_image_from_running_container():
    source = (ROOT / "scripts" / "deploy_production.sh").read_text(encoding="utf-8")

    assert "{{.Config.Image}}" in source
    assert "docker image inspect --format '{{index .RepoDigests 0}}'" not in source


def test_post_deploy_cleanup_never_force_removes_images():
    source = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")

    assert "docker image prune --all --force" in source
    assert "docker image rm -f" not in source
