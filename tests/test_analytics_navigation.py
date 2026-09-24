from pathlib import Path
import subprocess


def test_client_navigation_records_pageviews_without_duplicate_initial_load():
    subprocess.run(
        ["node", "tests/analytics_navigation.cjs"],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
