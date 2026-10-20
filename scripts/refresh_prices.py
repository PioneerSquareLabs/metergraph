"""Plan a low-risk catalog refresh from the official sources in the registry.

For each low-risk source (or each source named with ``--sources``) the
script fetches the page from its registered URL -- and from no other URL --
or reads ``<source-id>.*`` from ``--content-dir``. It checks the page's
structural fingerprint, extracts prices, checks invariants and the source's
bounds, and compares them with the supplied catalog. It prints a JSON report
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


def read_content(content_dir: Path, source_id: str) -> str | None:
    matches = sorted(content_dir.glob(f"{source_id}.*"))
    return matches[0].read_text(errors="ignore") if matches else None


def select_sources(registry: SourceRegistry, names: str | None) -> SourceRegistry:
    """The registry narrowed to the named sources, or to its low-risk ones."""
    if names:
        chosen = [registry.get(name.strip()) for name in names.split(",") if name.strip()]
    else:
        chosen = list(registry.low_risk())
    return SourceRegistry(registry.schema_version, tuple(chosen))


def gather(
    registry: SourceRegistry, content_dir: Path | None, allowed: frozenset[str]
) -> tuple[dict[str, str], dict[str, str]]:
    """Content per source id, and the reason each missing one is missing."""
    contents: dict[str, str] = {}
    errors: dict[str, str] = {}
    for source in registry.sources:
        if content_dir is not None:
            content = read_content(content_dir, source.id)
            if content is None:
                errors[source.id] = f"no {source.id}.* file in {content_dir}"
            else:
                contents[source.id] = content
            continue
        try:
            contents[source.id] = fetch_text(source.url, allowed)
        except (OSError, ValueError) as exc:
            errors[source.id] = f"fetch failed: {exc}"
    return contents, errors


def write_evidence(evidence_dir: Path, registry: SourceRegistry, contents: dict[str, str], document: dict, observed_on: date) -> None:
    """One evidence file per fetched source. Raw content is kept only where
    the registry's retention policy allows storing it."""
    evidence_dir.mkdir(parents=True, exist_ok=True)
    for source in registry.sources:
        content = contents.get(source.id)
        if content is None:
            continue
        evidence = discovery_evidence(source, content, document, observed_on=observed_on)
        if source.evidence.store_raw:
            evidence["raw_content"] = content
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
        contents, errors = gather(registry, args.content_dir, full_registry.allowed_urls())
        result = plan_low_risk_refresh(document, registry, contents, observed_on=args.observed_on)
    except (OSError, ValueError, SourceRegistryError, yaml.YAMLError) as exc:
        print(f"refresh_prices: {exc}", file=sys.stderr)
        return 1

    report = {"schema_version": REPORT_SCHEMA_VERSION, "observed_on": args.observed_on.isoformat()}
    report.update(result.as_json())
    for escalation in report["escalations"]:
        if escalation["reason"] == "content_missing" and escalation["source_id"] in errors:
            escalation["detail"] = errors[escalation["source_id"]]
    text = json.dumps(report, indent=2) + "\n"
    print(text, end="")
    if args.json:
        args.json.write_text(text)
    if args.evidence_dir:
        write_evidence(args.evidence_dir, registry, contents, document, args.observed_on)
    return 2 if result.delta["operations"] or result.escalations else 0


if __name__ == "__main__":
    sys.exit(main())
