#!/usr/bin/env python3
"""Render a reviewed additive pricing delta without rewriting catalog comments.

Only ``add_model``, ``add_alias`` and ``add_price`` are accepted. The output
is written to a new path, parsed again, and compared with the pure delta
application before it is kept.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Mapping

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "core" / "src"))

import yaml  # noqa: E402

from metergraph_core.catalog_delta import (  # noqa: E402
    CatalogDeltaError,
    apply_catalog_delta,
    parse_catalog_delta,
)
from metergraph_core.loader import _canonical_json, load_catalog_document  # noqa: E402

_MODEL = re.compile(r"^  - canonical_id:\s*(.+?)\s*$")
_ADDITIVE = {"add_model", "add_alias", "add_price"}


class ApplyDeltaError(ValueError):
    pass


class _IndentedDumper(yaml.SafeDumper):
    def increase_indent(self, flow: bool = False, indentless: bool = False):
        return super().increase_indent(flow, False)


def _canonical_id(line: str) -> str | None:
    match = _MODEL.match(line.rstrip("\n"))
    if match is None:
        return None
    parsed = yaml.safe_load(f"value: {match.group(1)}")
    value = parsed.get("value") if isinstance(parsed, Mapping) else None
    return str(value) if value is not None else None


def _model_bounds(lines: list[str], canonical_id: str) -> tuple[int, int]:
    starts = [index for index, line in enumerate(lines) if _canonical_id(line) == canonical_id]
    if len(starts) != 1:
        raise ApplyDeltaError(
            f"catalog needs exactly one model {canonical_id!r}; found {len(starts)}"
        )
    start = starts[0]
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if _MODEL.match(lines[index].rstrip("\n")) or re.match(
            r"^[a-z][a-z0-9_]*:\s*", lines[index]
        ):
            end = index
            break
    return start, end


def _sequence_item(value: Mapping[str, Any], indent: int) -> list[str]:
    rendered = yaml.dump(
        [dict(value)],
        Dumper=_IndentedDumper,
        sort_keys=False,
        default_flow_style=False,
        width=1000,
    )
    prefix = " " * indent
    return [prefix + line if line.strip() else line for line in rendered.splitlines(True)]


def render_additive_delta(catalog_text: str, delta_document: Mapping[str, Any]) -> str:
    """Return catalog text with a reviewed additive delta inserted."""
    try:
        document = yaml.safe_load(catalog_text)
        load_catalog_document(document)
        delta = parse_catalog_delta(delta_document)
    except (yaml.YAMLError, CatalogDeltaError, ValueError) as exc:
        raise ApplyDeltaError(str(exc)) from exc
    unsupported = [operation.op for operation in delta.operations if operation.op not in _ADDITIVE]
    if unsupported:
        raise ApplyDeltaError("only additive operations can be rendered")
    expected = apply_catalog_delta(document, delta_document)
    lines = catalog_text.splitlines(True)
    for operation in delta.operations:
        if operation.op == "add_model":
            insertion = next(
                (
                    index
                    for index, line in enumerate(lines)
                    if re.match(r"^[a-z][a-z0-9_]*:\s*", line)
                    and not line.startswith(("version:", "currency:", "pricing_verified_at:", "models:"))
                ),
                len(lines),
            )
            lines[insertion:insertion] = _sequence_item(operation.model, 2)
            continue
        _, end = _model_bounds(lines, operation.key.canonical_id)
        if operation.op == "add_alias":
            insertion = next(
                (
                    index
                    for index in range(*_model_bounds(lines, operation.key.canonical_id))
                    if lines[index].startswith("    prices:")
                ),
                None,
            )
            if insertion is None:
                raise ApplyDeltaError(
                    f"model {operation.key.canonical_id!r} has no prices block"
                )
            lines[insertion:insertion] = _sequence_item(operation.alias, 6)
        else:
            lines[end:end] = _sequence_item(operation.price, 6)
    rendered = "".join(lines)
    try:
        actual = yaml.safe_load(rendered)
        load_catalog_document(actual)
    except (yaml.YAMLError, ValueError) as exc:
        raise ApplyDeltaError(f"rendered catalog is invalid: {exc}") from exc
    if _canonical_json(actual) != _canonical_json(expected):
        raise ApplyDeltaError("rendered catalog differs from the validated delta result")
    return rendered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--delta", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--reviewed",
        action="store_true",
        help="confirm the proposed operations and evidence were reviewed",
    )
    args = parser.parse_args(argv)
    try:
        if not args.reviewed:
            raise ApplyDeltaError("--reviewed is required")
        if args.catalog.resolve() == args.out.resolve():
            raise ApplyDeltaError("output must differ from the input catalog")
        if args.out.exists():
            raise ApplyDeltaError(f"output already exists: {args.out}")
        delta = yaml.safe_load(args.delta.read_text())
        rendered = render_additive_delta(args.catalog.read_text(), delta)
        args.out.write_text(rendered)
    except (OSError, yaml.YAMLError, ApplyDeltaError) as exc:
        print(f"apply_price_delta: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
