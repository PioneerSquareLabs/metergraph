"""Plan a low-risk catalog refresh from the official sources in the registry.

For each low-risk source (or each source named with ``--sources``) the
script fetches the page from its registered URL -- and from no other URL --
or reads ``<source-id>.*`` from ``--content-dir``. It checks the page's
structural fingerprint, extracts prices, checks invariants and the source's
allowed dimensions, and compares them with the supplied catalog. It prints a JSON report
with a pricing delta, one observation per source (including sources with no
change), and every escalation.

It uses no LLM, holds no credentials, and never applies the delta, writes
the catalog, publishes, or touches GitHub.

Usage:

    python scripts/refresh_prices.py [--catalog prices.yaml] [--registry sources.yaml]
        [--sources id,...] [--content-dir DIR] [--observed-on YYYY-MM-DD]
        [--json report.json] [--evidence-dir DIR]

Exit status: 0 no change, 2 a delta or an escalation, 1 an error.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.request import (
    HTTPRedirectHandler,
    OpenerDirector,
    Request,
    build_opener,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
# Run against the in-repository core, not whatever version is installed.
_CORE_SOURCE = str(REPOSITORY_ROOT / "core" / "src")
if _CORE_SOURCE not in sys.path:
    sys.path.insert(0, _CORE_SOURCE)

import yaml  # noqa: E402

from metergraph_core.loader import DEFAULT_CATALOG_PATH  # noqa: E402
from metergraph_core.refresh import discovery_evidence, plan_low_risk_refresh  # noqa: E402
from metergraph_core.sources import (  # noqa: E402
    DEFAULT_REGISTRY_PATH,
    SourceRegistry,
    SourceRegistryError,
    load_source_registry,
)

USER_AGENT = "metergraph-refresh-prices/1 (+https://github.com/PioneerSquareLabs/metergraph)"
REPORT_SCHEMA_VERSION = 1


class AllowlistRedirectHandler(HTTPRedirectHandler):
    """Follow a redirect only to an exact registered URL. Every hop is
    checked, so a redirect can never lead a fetch off the allowlist."""

    def __init__(self, allowed: frozenset[str]) -> None:
        super().__init__()
        self.allowed = allowed

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        if newurl not in self.allowed:
            raise ValueError(f"redirect to {newurl} is not a registered source URL")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def build_allowlisted_opener(allowed: frozenset[str]) -> OpenerDirector:
    return build_opener(AllowlistRedirectHandler(allowed))


def fetch_text(url: str, allowed: frozenset[str], timeout: float = 30.0) -> str:
    """Fetch a registered source URL. Any other URL, including any redirect
    target, is refused."""
    if url not in allowed:
        raise ValueError(f"{url} is not a registered source URL")
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with build_allowlisted_opener(allowed).open(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="ignore")


def content_file(content_dir: Path, source_id: str) -> Path | None:
    """The ``<source-id>.*`` file a content directory supplies, if any."""
    matches = sorted(content_dir.glob(f"{source_id}.*"))
    return matches[0] if matches else None


def read_content(content_dir: Path, source_id: str) -> str | None:
    path = content_file(content_dir, source_id)
    return path.read_text(errors="ignore") if path is not None else None


def _same_file(first: Path, second: Path) -> bool:
    """Whether two paths name one file, following symlinks. A path that does
    not exist yet is compared by its resolved location."""
    try:
        return first.samefile(second)
    except OSError:
        return first.resolve() == second.resolve()


def refuse_overwrites(outputs: list[Path], inputs: list[Path]) -> None:
    """Refuse before anything is written when an output would replace an
    input the run read, or another output of the same run."""
    for index, output in enumerate(outputs):
        for source in inputs:
            if _same_file(output, source):
                raise ValueError(f"refusing to write {output}: it is the input {source}")
        for other in outputs[:index]:
            if _same_file(output, other):
                raise ValueError(f"refusing to write {output}: it is also the output {other}")


def select_sources(registry: SourceRegistry, names: str | None) -> SourceRegistry:
    """The registry narrowed to the named sources, or to its low-risk ones."""
    if names:
        # A source named twice is planned once, in first-seen order.
        unique = dict.fromkeys(name.strip() for name in names.split(",") if name.strip())
        chosen = [registry.get(name) for name in unique]
    else:
        chosen = list(registry.low_risk())
    return SourceRegistry(registry.schema_version, tuple(chosen))


def gather(
    registry: SourceRegistry, content_dir: Path | None, allowed: frozenset[str]
) -> tuple[dict[str, str], dict[str, str], list[Path]]:
    """Content per source id, the reason each missing one is missing, and
    the content files read."""
    contents: dict[str, str] = {}
    errors: dict[str, str] = {}
    read: list[Path] = []
    for source in registry.sources:
        if content_dir is not None:
            path = content_file(content_dir, source.id)
            if path is None:
                errors[source.id] = f"no {source.id}.* file in {content_dir}"
            else:
                contents[source.id] = path.read_text(errors="ignore")
                read.append(path)
            continue
        try:
            contents[source.id] = fetch_text(source.url, allowed)
        except (OSError, ValueError) as exc:
            errors[source.id] = f"fetch failed: {exc}"
    return contents, errors, read


def write_error(exc: OSError) -> str:
    """A failed write as the path and the operating system's reason only."""
    return f"cannot write {exc.filename or 'output'}: {exc.strerror or type(exc).__name__}"


def evidence_paths(evidence_dir: Path, registry: SourceRegistry, contents: dict[str, str]) -> list[Path]:
    """The evidence files write_evidence would write."""
    return [evidence_dir / f"{source.id}.json" for source in registry.sources if source.id in contents]


def write_evidence(evidence_dir: Path, registry: SourceRegistry, contents: dict[str, str], document: dict, observed_on: date) -> None:
    """One evidence file per fetched source, holding only fingerprints and
    normalized facts; raw page content is never written."""
    evidence_dir.mkdir(parents=True, exist_ok=True)
    for source in registry.sources:
        content = contents.get(source.id)
        if content is None:
            continue
        evidence = discovery_evidence(source, content, document, observed_on=observed_on)
        (evidence_dir / f"{source.id}.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG_PATH)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY_PATH)
    parser.add_argument("--sources", help="comma-separated registry source ids (default: every low-risk source)")
    parser.add_argument("--content-dir", type=Path, help="read <source-id>.* files here instead of fetching")
    parser.add_argument("--observed-on", type=date.fromisoformat, default=datetime.now(timezone.utc).date())
    parser.add_argument("--json", type=Path, help="also write the report here")
    parser.add_argument("--evidence-dir", type=Path, help="write per-source evidence files here")
    args = parser.parse_args(argv)

    try:
        document = yaml.safe_load(args.catalog.read_text())
        full_registry = load_source_registry(args.registry)
        registry = select_sources(full_registry, args.sources)
        contents, errors, read = gather(registry, args.content_dir, full_registry.allowed_urls())
        result = plan_low_risk_refresh(document, registry, contents, observed_on=args.observed_on)
        # Nothing is written, not even the report on stdout, until no output
        # would replace an input or another output.
        outputs = [args.json] if args.json else []
        if args.evidence_dir:
            outputs += evidence_paths(args.evidence_dir, registry, contents)
        refuse_overwrites(outputs, [args.catalog, args.registry] + read)
    except (OSError, ValueError, SourceRegistryError, yaml.YAMLError) as exc:
        print(f"refresh_prices: {exc}", file=sys.stderr)
        return 1

    report = {"schema_version": REPORT_SCHEMA_VERSION, "observed_on": args.observed_on.isoformat()}
    report.update(result.as_json())
    for escalation in report["escalations"]:
        if escalation["reason"] == "content_missing" and escalation["source_id"] in errors:
            escalation["detail"] = errors[escalation["source_id"]]
    # Files are written before the report reaches stdout, so a failed write
    # exits 1 with an error naming the path and the reason, and no report.
    try:
        text = json.dumps(report, indent=2) + "\n"
        if args.evidence_dir:
            write_evidence(args.evidence_dir, registry, contents, document, args.observed_on)
        if args.json:
            args.json.write_text(text)
    except OSError as exc:
        print(f"refresh_prices: {write_error(exc)}", file=sys.stderr)
        return 1
    print(text, end="")
    return 2 if result.delta["operations"] or result.escalations else 0


if __name__ == "__main__":
    sys.exit(main())
