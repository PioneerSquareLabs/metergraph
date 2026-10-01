# Public repository confidentiality

This repository is public. Treat every committed file and all GitHub metadata as
world-readable and permanently copyable.

## Confidentiality boundary

Never disclose a non-public customer, prospect, partner, or evaluation target,
or reveal that MeterGraph is working with one. Do not include identifying or
customer-derived information in:

- source code, tests, fixtures, snapshots, examples, benchmarks, documentation,
  generated artifacts, screenshots, or logs;
- file and directory names, branch names, commit messages, pull request titles
  and bodies, review comments, issues, release notes, or workflow output;
- sample prompts, request or response content, traces, workload names, support
  notes, incidents, onboarding details, or operational measurements.

Identifying information includes names, domains, email addresses, tenant or
workspace names and IDs, account IDs, cloud resource identifiers, URLs,
repository names, infrastructure details, and any combination of dates, counts,
models, workloads, or business facts that could identify an organization.

A value is still confidential if it is truncated, hashed, encoded, lightly
renamed, or already present in this repository's Git history. Prior accidental
publication is not authorization to repeat it.

## Source boundaries

Do not copy material from private repositories, production systems, customer
environments, support or sales systems, internal tickets, chat, email, local
captures, or private documents into this repository unless a human explicitly
authorizes that exact public disclosure.

When private evidence motivates a change, describe the durable technical
behavior without attribution. Rebuild tests and examples with synthetic data
instead of editing real customer material in place.

## Synthetic data

Use obviously fictional, purpose-based examples. Prefer reserved domains such
as `example.com`, generic labels such as `example-workspace`, and freshly
generated non-production identifiers. Synthetic content must preserve only the
minimum structure needed to test the behavior.

Never use a real customer name as a test label, fixture name, helper name, or
mnemonic.

## Mandatory public-change gate

Before committing, pushing, or opening or updating a pull request:

1. Inspect `git status --short`, including untracked files.
2. Review the complete staged diff, filenames, binary or generated artifacts,
   and deleted-file replacements.
3. Search the proposed changes for customer identifiers, private infrastructure,
   credentials, local filesystem paths, captured content, and internal-only
   terminology.
4. Review the branch name, commit message, pull request title and body, and any
   planned comment using the same standard.
5. Confirm every example and fixture is synthetic and cannot be linked to a
   real organization.
6. If provenance or identifiability is uncertain, stop and ask for human review.
   Do not commit, push, or publish while uncertain.

Secret scanners are required where configured, but they are not sufficient.
Customer identities and relationships are confidential even when no credential
or conventional secret is present.

## Existing exposure

If you discover potentially identifying material already published, do not
repeat it in a public issue, commit message, pull request, or review comment.
Report it privately with neutral labels, preserve the evidence needed for a
private audit, and wait for an approved remediation plan.
