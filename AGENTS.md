# Public repository privacy

This repository is public. Do not put a non-public customer or prospect name,
domain, tenant or workspace ID, captured prompt or response, private path, or
cloud account or resource ID in any public file or GitHub text. This includes
branch names, commit messages, pull requests, issues, comments, and logs, and
anything that reveals MeterGraph is working with an organization.

Use `example.com`, synthetic identifiers, and purpose-based test and fixture
names. Recreate the minimum synthetic case instead of adapting customer
material.

Report an existing exposure privately. Do not quote or name it in the public
fix, branch, commit, pull request, issue, or comment. If unsure whether
something is identifying or public, stop and ask before publishing.

## Public contribution workflow

Write self-contained public branch names, commits, issues, pull requests,
comments, and release notes. Keep private tracker IDs, links, discussion, and
unpublished plans in the internal tracker. Review the exact diff and metadata
before pushing. Run `python3 scripts/check_publication.py origin/main` before
publishing; CI repeats common reference checks but cannot undo a disclosure.
Report suspected vulnerabilities privately to maintainers. A historical
harmless tracker ID alone does not require
rewriting Git history.
