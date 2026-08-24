from pathlib import Path
import subprocess
import sys


def test_core_package_artifacts_are_publishable():
    subprocess.run(
        [sys.executable, "core/tests/package/verify_artifacts.py"],
        cwd=Path(__file__).resolve().parents[2],
        check=True,
    )
