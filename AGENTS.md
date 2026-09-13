# Working on TOTISH

## Scope and completion

- Follow the current task's scope. An audit produces findings, not code changes.
- For an authorized code fix, continue through implementation, relevant regression checks, a focused PR and the existing CI. Stop earlier when the user requested a narrower deliverable.
- Proceed with already authorized edits and safe tests without asking again. Ask only when missing information blocks a consequential decision or an action needs additional authorization.
- Merge, deploy and production-data changes require explicit authorization for the task; creating a PR does not authorize them.

## Read and verify what the change needs

- Inspect the affected code and callers. Consult documentation for the relevant area, rather than reading every project document before each edit.
- For scoring/recalculation, use `docs/SCORING_RECALCULATION.md`; for schema changes, `docs/DB_MIGRATIONS.md`; for deployment work, `docs/PRODUCTION_HOST_CONTROL.md`. Check current code when older documentation disagrees.
- Start with checks of the changed behavior. Rerun checks when code changes, a check fails, or a concrete uncertainty remains; a passing check on unchanged code need not be repeated locally.
- Before declaring a PR ready, wait for its required checks on the final revision. Use `.github/workflows/ci.yml` as the source of commands and test-environment configuration. Do not disable checks to obtain a green result.
- PostgreSQL integration tests use the disposable CI service and `AUTO_RESULTS_TEST_DATABASE_URL`. Do not point tests at production. Use existing test infrastructure; additional cloud provisioning is a separate task, not a prerequisite for ordinary fixes.
- For documentation-only changes, check the diff and referenced paths; do not add tests that merely assert wording or duplicate the full CI suite locally.

## Preserve production invariants

- Keep the canonical points formula in `app/models/scoring.py` unchanged unless the task explicitly changes game rules. Reuse `app/services/scoring_recalculation_service.py` for recalculation.
- Preserve manual-result priority, match identity checks, match-before-predictions lock order, and atomic result/points/outbox updates.
- Preserve source-consensus and fail-closed behavior. Read current timing policy from `scripts/auto_result_policy.py`; historical task prompts are not the current specification.
- Report what changed, meaningful verification results and concrete limitations. Distinguish repository/CI evidence from production behavior that was not observed.
