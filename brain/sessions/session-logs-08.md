# Development Log - Session 08

**Date**: 2026-07-17 (short bridge session after 07's checkout)
**Duration**: ~15 minutes
**Milestones**: servers restored (detached); next workstream scoped — productize prompt patching ("tuned-candidate assessments") so weaker models can be pushed toward parity inside the product, not in scratchpad scripts.

---

## Current State & How to Resume

### System State (as of session end)
- **Dashboard live** at 127.0.0.1:7300 and **fly proxy** on 15435, both started with
  `nohup ... & disown` (session-independent; logs at `/tmp/mg-serve.log`,
  `/tmp/mg-flyproxy.log`). Session-tracked background tasks kept getting reaped at
  session teardown — use nohup for anything meant to outlive the session.
- Repo synced with origin/main at `edae2db` (session 07 checkout). No uncommitted work.
- All session-07 state stands: run 20 (sonnet 40%), run 21 (haiku 20%) on set 3;
  holdout set 12 in DB; experiment artifacts + backups in
  `brain/experiments/prompt-tuning-2026-07-16/`; orphaned runs 12/13/19 still `running`.

### To Verify State
```bash
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:7300/spend   # 200
lsof -iTCP:15435 -sTCP:LISTEN                                        # flyctl
cd ~/software/metergraph && .venv/bin/python -m pytest -q            # 39 passed
```
Restart if down: `nohup fly proxy 15435:5432 -a metergraph-db > /tmp/mg-flyproxy.log 2>&1 &`
then `nohup .venv/bin/mg serve > /tmp/mg-serve.log 2>&1 &`.

### To Resume Development — NEXT SESSION'S GOAL (Alex, stated 2026-07-17)
**Bake the prompt-patching methodology into the product**: run the session-07
train/test-split analysis as a first-class Metergraph capability, so a weaker model can
be assessed *with a tuned prompt* and pushed toward parity. Everything needed:

- **Methodology to productize** = `brain/decisions/2026-07-16-prompt-tuning-ceiling.md`:
  dev/holdout split (holdout must be call-disjoint and never iterated on), mechanical
  metrics for iteration (fences, length band, required fields, verdict flips, drift),
  judge only on the holdout with an unpatched same-sample control, generic-calibration-only
  rule (never answer-shaped guidance).
- **Working reference code** = `brain/experiments/prompt-tuning-2026-07-16/haiku_opt.py`
  (variant runner: patch placement system/user, prefill support, metrics) and
  `judge_outputs.py` (contract-judge scoring identical to run_candidate).
- **Proven results to reproduce**: haiku 17%→47% (v4: format+length+calibration in user
  message + `{` prefill), sonnet 60%→80% (s2: anti-harshness calibration + strict-JSON +
  field presence). Patch texts live in haiku_opt.py (`_V3_TEXT`, `_S1_TEXT`, `_S2_TEXT`).

Likely integration points (from session-07 known issues):
1. **Schema**: eval_runs needs a `prompt_patch` (and placement/prefill) column so a run is
   (candidate_model, patch) — today run_candidate deletes by (set, model) and can only
   replay verbatim payloads.
2. **`run_candidate`**: accept an optional payload transform (append patch to user msg /
   system, optional assistant prefill) — the experiment's `call()` shows the exact logic.
3. **`build_eval_set`**: needs a disjoint-sampling option (exclude call_ids already used
   by a func's other sets) — sets 2/3/7 all sampled the SAME 30 calls; set 12 was built
   by hand with exclusion (see its build_log).
4. **Dev/holdout orchestration**: an assess-level flow that builds the pair, iterates? —
   or (simpler first cut) accepts a hand-written patch, runs it dev-metrics-first, then
   holdout+control judged.
5. **Scorecard/dashboard**: render tuned rows ("sonnet-5 + patch · 80%") and the what-if
   slider story (at a customer's 80% bar, tuned sonnet = ~$630/mo on run_evaluation).
6. **Prefill caveat for adoption**: a tuned-haiku swap needs assistant-prefill support in
   the target app's calls, not just prompt text — installpr/docs implication.

### Checklist Status
| Phase | Status | Notes |
|-------|--------|-------|
| Restore servers after teardown | done | nohup-detached |
| Productize tuned-candidate assessments | **next session** | scope above |
| BadRequestError investigation (50% of run_evaluation) | pending | carried from 07 |
| Demo path (mg fix → snapshot → dry runs) | pending | carried from 05 |

---

## Executive Summary

Bridge session: the fly tunnel and dashboard died with session teardown (harness reaps
session-tracked background tasks); both are back via nohup and verified serving. No code
changes. The real content is direction: Alex wants the session-07 prompt-tuning
experiment — train/test-split prompt patching that took haiku 17%→47% and sonnet 60%→80%
on a disjoint holdout — turned into a product capability ("tuned-candidate assessments"),
so Metergraph can push weaker models toward parity as part of its standard swap
assessment, not as a one-off analysis. Scope and integration points captured above;
methodology and reference code are already in the brain from session 07.

## Known Issues & Technical Debt
Unchanged from session 07 (BadRequestError thread, orphaned runs 12/13/19, fn_desc cache,
shared Fly identity, Caleb's unrecorded app/ workstream, PR workflow question).

## Next Steps
1. Tuned-candidate assessments (scope above) — next session, post-compact.
2. Then the carried threads: BadRequestError, demo path, Caleb sync.
