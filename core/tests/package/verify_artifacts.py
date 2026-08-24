#!/usr/bin/env python3
"""Gate the built ``metergraph-core`` package shape.

Builds (or, with ``--no-build``, reuses) exactly one sdist and one wheel, then:

1. checks the distribution name, version, and ``Requires-Python`` metadata;
2. confirms each artifact ships ``__init__.py``, ``catalog.py``, ``loader.py``
   and exactly one bundled ``data/prices.yaml``;
3. rejects top-level test packages in the installed wheel;
4. installs the wheel into a throwaway virtualenv that cannot see the source
   tree; and
5. loads the bundled catalog there and reproduces the golden price.

Archive members are listed and read in memory only. This script never extracts
untrusted archive paths onto disk.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import venv
import zipfile
from email.parser import BytesParser
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parents[2]  # core/
DIST_DIR = CORE_DIR / "dist"

EXPECTED_NAME = "metergraph-core"
EXPECTED_VERSION = "0.1.0"
EXPECTED_REQUIRES_PYTHON = ">=3.10"
GOLDEN_COST = "0.52500000"
GOLDEN_PRICE_ID = "openai/gpt-5.4-mini:openai-api:global:2026-03-17"
CATALOG_VERSION = "2026-08-24"

REQUIRED_MODULES = ("__init__.py", "catalog.py", "loader.py")


class VerifyError(AssertionError):
    """Raised when a built artifact violates the release contract."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise VerifyError(message)


def _clean_dist() -> None:
    if not DIST_DIR.exists():
        return
    for artifact in list(DIST_DIR.glob("*.whl")) + list(DIST_DIR.glob("*.tar.gz")):
        artifact.unlink()


def _build() -> None:
    subprocess.run(
        [sys.executable, "-m", "build", "--outdir", str(DIST_DIR), str(CORE_DIR)],
        check=True,
    )


def _single(pattern: str) -> Path:
    matches = sorted(DIST_DIR.glob(pattern))
    _require(
        len(matches) == 1,
        f"expected exactly one {pattern} in {DIST_DIR}, found {len(matches)}: "
        f"{[m.name for m in matches]}",
    )
    return matches[0]


def _parse_metadata(raw: bytes) -> dict[str, str]:
    message = BytesParser().parsebytes(raw)
    return {key: message.get(key, "") for key in ("Name", "Version", "Requires-Python")}


def _check_metadata(fields: dict[str, str], source: str) -> None:
    _require(
        fields["Name"] == EXPECTED_NAME,
        f"{source}: Name {fields['Name']!r} != {EXPECTED_NAME!r}",
    )
    _require(
        fields["Version"] == EXPECTED_VERSION,
        f"{source}: Version {fields['Version']!r} != {EXPECTED_VERSION!r}",
    )
    _require(
        fields["Requires-Python"] == EXPECTED_REQUIRES_PYTHON,
        f"{source}: Requires-Python {fields['Requires-Python']!r} != "
        f"{EXPECTED_REQUIRES_PYTHON!r}",
    )


def _has_test_segment(path: str) -> bool:
    segments = [segment for segment in path.split("/") if segment]
    return "tests" in segments or any(
        segment == "test" or segment.startswith("test_") for segment in segments
    )


def _verify_wheel(wheel: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata_names = [n for n in names if n.endswith(".dist-info/METADATA")]
        _require(
            len(metadata_names) == 1,
            f"{wheel.name}: expected one METADATA, found {metadata_names}",
        )
        _check_metadata(_parse_metadata(archive.read(metadata_names[0])), wheel.name)

        payload = [n for n in names if not n.split("/")[0].endswith(".dist-info")]
        # Everything installed must live under the import package.
        for name in payload:
            top = name.split("/")[0]
            _require(
                top == "metergraph_core",
                f"{wheel.name}: unexpected top-level member {name!r}",
            )
        # No test package must ride along into installs.
        for name in payload:
            _require(
                not _has_test_segment(name),
                f"{wheel.name}: test artifact must not be packaged: {name!r}",
            )
        for module in REQUIRED_MODULES:
            _require(
                f"metergraph_core/{module}" in names,
                f"{wheel.name}: missing metergraph_core/{module}",
            )
        catalogs = [n for n in names if n.endswith("data/prices.yaml")]
        _require(
            catalogs == ["metergraph_core/data/prices.yaml"],
            f"{wheel.name}: expected exactly one bundled catalog, found {catalogs}",
        )


def _verify_sdist(sdist: Path) -> None:
    with tarfile.open(sdist, "r:gz") as archive:
        members = archive.getnames()
        pkg_info = [m for m in members if m.endswith("/PKG-INFO")]
        _require(
            len(pkg_info) >= 1,
            f"{sdist.name}: missing PKG-INFO",
        )
        # Shortest path is the top-level PKG-INFO.
        top_pkg_info = min(pkg_info, key=len)
        handle = archive.extractfile(top_pkg_info)
        _require(handle is not None, f"{sdist.name}: PKG-INFO not readable")
        _check_metadata(_parse_metadata(handle.read()), sdist.name)

        # The sdist must exclude tests too, not just the wheel. Members are
        # prefixed with the "<name>-<version>/" root directory; strip it before
        # inspecting path segments.
        for member in members:
            _, _, relative = member.partition("/")
            _require(
                not _has_test_segment(relative),
                f"{sdist.name}: test artifact must not be packaged: {member!r}",
            )

        for module in REQUIRED_MODULES:
            _require(
                any(m.endswith(f"metergraph_core/{module}") for m in members),
                f"{sdist.name}: missing metergraph_core/{module}",
            )
        catalogs = [m for m in members if m.endswith("metergraph_core/data/prices.yaml")]
        _require(
            len(catalogs) == 1,
            f"{sdist.name}: expected exactly one bundled catalog, found {catalogs}",
        )


def _venv_python(env_dir: Path) -> Path:
    if os.name == "nt":
        return env_dir / "Scripts" / "python.exe"
    return env_dir / "bin" / "python"


def _verify_isolated_install(wheel: Path) -> None:
    check = textwrap.dedent(
        f"""
        from datetime import datetime, timezone

        import metergraph_core
        from metergraph_core import load_catalog

        source = getattr(metergraph_core, "__file__", "")
        assert "site-packages" in source, source

        loaded = load_catalog()
        assert loaded.version == {CATALOG_VERSION!r}, loaded.version
        assert loaded.currency == "USD", loaded.currency
        assert loaded.pricing_verified_at.isoformat() == "2026-08-24"
        assert len(loaded.content_hash) == 64, loaded.content_hash
        deployment = loaded.snapshot.resolve_price(
            model="moonshotai/kimi-k3",
            channel="vercel-ai-gateway",
            at=datetime(2026, 8, 24, tzinfo=timezone.utc),
        )
        assert deployment is not None
        assert str(deployment.price.input_per_mtok) == "2.9"
        assert str(deployment.price.output_per_mtok) == "14.0"
        assert deployment.price.source_url == "https://vercel.com/ai-gateway/models/kimi-k3/providers"
        result = loaded.snapshot.cost(
            provider="openai",
            model="gpt-5.4-mini",
            at=datetime(2026, 8, 17, tzinfo=timezone.utc),
            input_tokens=100_000,
            output_tokens=100_000,
        )
        assert result.price_id == {GOLDEN_PRICE_ID!r}, result.price_id
        assert str(result.cost_usd) == {GOLDEN_COST!r}, result.cost_usd
        assert result.status == "priced", result.status
        print("GOLDEN", result.cost_usd)
        """
    )
    # Strip PYTHONPATH so an ambient source-tree entry (e.g. a test harness
    # exporting PYTHONPATH=core/src) cannot satisfy the import instead of the
    # installed wheel.
    child_env = os.environ.copy()
    child_env.pop("PYTHONPATH", None)
    with tempfile.TemporaryDirectory() as tmp:
        env_dir = Path(tmp) / "venv"
        venv.create(env_dir, with_pip=True)
        python = _venv_python(env_dir)
        subprocess.run(
            [str(python), "-m", "pip", "install", "--quiet", str(wheel)],
            check=True,
            env=child_env,
        )
        # Run from a directory with no source tree on sys.path so only the
        # installed wheel can satisfy the import.
        completed = subprocess.run(
            [str(python), "-c", check],
            cwd=tmp,
            check=True,
            capture_output=True,
            text=True,
            env=child_env,
        )
        _require(
            f"GOLDEN {GOLDEN_COST}" in completed.stdout,
            f"isolated install did not reproduce golden price: {completed.stdout!r}",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-build",
        action="store_true",
        help="verify the prebuilt artifacts already in core/dist without rebuilding",
    )
    args = parser.parse_args()

    if args.no_build:
        _require(
            DIST_DIR.is_dir(),
            f"--no-build requires prebuilt artifacts in {DIST_DIR}",
        )
    else:
        _clean_dist()
        _build()

    wheel = _single("*.whl")
    sdist = _single("*.tar.gz")

    _verify_wheel(wheel)
    _verify_sdist(sdist)
    _verify_isolated_install(wheel)

    print(f"OK: {sdist.name}, {wheel.name} verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
