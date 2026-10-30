"""Exercise the production bootstrap shell with Docker at its boundary."""
import os
from pathlib import Path
import subprocess
import textwrap

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("mode, success", [
    ("cached", True), ("primary", True), ("mirror", True),
    ("unavailable", False), ("wrong_version", False), ("holder_failure", False),
])
def test_backup_tools_bootstrap_preserves_checks_and_cached_image(tmp_path, mode, success):
    workflow = (ROOT / ".github/workflows/deploy.yml").read_text()
    block = textwrap.dedent(workflow.split("# BEGIN BACKUP IMAGE BOOTSTRAP\n", 1)[1]
                            .split("# END BACKUP IMAGE BOOTSTRAP", 1)[0])
    log = tmp_path / "docker-calls"
    fake_docker = r'''
docker() {
    printf '%s\n' "$*" >> "$CALL_LOG"
    case "$1 $2" in
        'image inspect') [[ "$MODE" == cached ]] ;;
        'pull postgres:17') [[ "$MODE" == primary ]] ;;
        'pull public.ecr.aws/docker/library/postgres:17') [[ "$MODE" != unavailable ]] ;;
        'run --rm')
            if [[ "$MODE" == wrong_version ]]; then
                echo 'pg_dump (PostgreSQL) 16.11'
            else
                echo 'pg_dump (PostgreSQL) 17.11'
            fi ;;
        'create --name') [[ "$MODE" != holder_failure ]] ;;
        *) return 0 ;;
    esac
}
'''
    result = subprocess.run(["bash", "-c", "set -Eeuo pipefail\n" + fake_docker + block],
                            env=dict(os.environ, MODE=mode, CALL_LOG=str(log)),
                            capture_output=True, text=True)
    calls = log.read_text().splitlines()
    assert (result.returncode == 0) is success, result.stderr
    if mode == "cached":
        assert not any(call.startswith("pull ") for call in calls)
    if mode == "primary":
        assert not any("public.ecr.aws" in call for call in calls)
    if mode == "mirror":
        assert "tag public.ecr.aws/docker/library/postgres:17 postgres:17" in calls
    holders = [call for call in calls if call.startswith("create --name")]
    if success:
        assert len(holders) == 1
        assert "--mount type=tmpfs,destination=/var/lib/postgresql/data" in holders[0]
        assert "--entrypoint /bin/true postgres:17" in holders[0]
    elif mode != "holder_failure":
        assert holders == []
