from pathlib import Path
import subprocess
import sys


def test_server_wheel_declares_and_prices_through_core():
    subprocess.run(
        [sys.executable, "server/tests/package/verify_server_artifacts.py"],
        cwd=Path(__file__).resolve().parents[2],
        check=True,
    )
