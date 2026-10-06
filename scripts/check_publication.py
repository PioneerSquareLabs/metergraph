"""Check common internal references before a public push or PR.

Usage: python3 scripts/check_publication.py origin/main
This cannot detect all private data. Review the full diff and metadata yourself.
"""

import json
import os
import re
import subprocess
import sys


PATTERNS = {
    "internal issue ID": re.compile(r"\b" + "MET" + r"-[0-9]+\b", re.I),
    "internal tracker URL": re.compile("linear.app/" + "metergraph", re.I),
    "private repository reference": re.compile("PioneerSquareLabs/" + "metergraph-internal", re.I),
}


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True)


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "origin/main"
    try:
        metadata = [
            ("new commits", git("log", "--format=%B", f"{base}..HEAD")),
        ]
        diff = git("diff", "--no-ext-diff", "--unified=0", f"{base}...HEAD", "--")
    except subprocess.CalledProcessError as error:
        print(f"Could not compare against {base}: {error}", file=sys.stderr)
        return 2

    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if event_path:
        with open(event_path, encoding="utf-8") as event_file:
            pull_request = json.load(event_file).get("pull_request", {})
        metadata.extend(
            [
                ("PR title", pull_request.get("title") or ""),
                ("PR body", pull_request.get("body") or ""),
            ]
        )

    added_lines = "\n".join(
        line[1:]
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    metadata.append(("added lines", added_lines))

    branch = git("branch", "--show-current").strip()
    if event_path:
        branch = pull_request.get("head", {}).get("ref") or branch
    if PATTERNS["internal issue ID"].search(branch):
        print("Review branch name: it appears to contain an internal issue ID.")

    findings = [
        f"{location}: {name}"
        for location, content in metadata
        for name, pattern in PATTERNS.items()
        if pattern.search(content)
    ]
    if findings:
        print("Check these public references before publishing:")
        print("\n".join(f"- {finding}" for finding in findings))
        return 1

    print("No known internal references found. Review for other private data before publishing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
