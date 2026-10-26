# Operational dispatch contract

Status: WIP implementation on PR #3; not deployed and not approved for merge.

## Trust boundaries

- Read-only evidence endpoints continue to use `X-API-Key`.
- The operations router uses a separate `X-Ops-Key`; it must not reuse the read-only credential.
- Only `lite` and `plus` repositories are allowlisted, and only the fixed workflow `.github/workflows/certification-runner.yml` on the fixed `main` ref can be dispatched.
- The runner, not the API or the language model, performs repository mutations.
- GitHub credentials must be stored as server/Actions secrets and must never be returned, logged, or included in prompts.
- No arbitrary workflow names, refs, repository names, shell commands, or file paths may be accepted from a caller.

## Initial operation allowlist

| Operation | Purpose | Default |
|---|---|---|
| `gate` | Request a gate already declared in certification continuity | `dry_run=true` |
| `docs` | Validate a documentation patch against the runner's strict path policy | `dry_run=true` |

Code-edit operation is intentionally excluded from this first rollout.

## Durable idempotency

- Every dispatch request requires `X-Idempotency-Key` (16–200 characters, restricted character set).
- The raw key is never stored; the API stores its SHA-256 digest and a SHA-256 digest of the canonical request body.
- A PostgreSQL uniqueness constraint reserves the key atomically before calling GitHub. A repeated key with the same body returns the existing operation state; a repeated key with a different body returns HTTP 409.
- The reservation is committed as `dispatching` before the network call. If the network outcome is ambiguous or the API crashes, the key remains reserved and will not dispatch again automatically. This favors preventing duplicate Actions runs over automatic recovery; an operator must reconcile the operation before deciding how to proceed.
- A confirmed GitHub HTTP 204 marks the operation `accepted`; a definite rejection is recorded as `rejected`. Neither means the workflow completed or the gate passed.
- The database is configured via `OPS_DATABASE_URL`. Missing/unavailable database or missing schema must fail closed. Apply `migrations/001_ops_idempotency.sql` manually to a dedicated active PostgreSQL database before any rollout.
- Do not use the suspended/expired Axis Suite database for this feature. The currently discovered Render database is suspended and expired; no active dedicated Precision Lab idempotency database has been verified.

## Dispatch semantics

1. Authenticate with constant-time comparison.
2. Validate the complete request before any GitHub network call.
3. Reserve the idempotency key durably before dispatch.
4. Dispatch only the fixed workflow and trusted ref.
5. Return acceptance only after GitHub confirms HTTP 204.
6. Report an operation identifier and explicitly state that acceptance is not completion or certification PASS.
7. Provide read-only status/evidence lookup separately; do not expose a caller-supplied arbitrary run URL.

## Rollout gates

- Unit and integration tests for auth, validation, idempotency collisions, concurrent reservations, ambiguous network failures, and no-network behavior for invalid requests.
- Verify GitHub token permissions and Render environment variable names without reading or returning secret values.
- Verify live health and authenticated read-only endpoints.
- Provision a dedicated active PostgreSQL database, apply the migration, and configure `OPS_DATABASE_URL`.
- Keep `OPS_DISPATCH_ENABLED` disabled until tests pass, storage is verified, and a reviewed end-to-end dry run is completed.
- Do not trigger product gates just to test transport.

## Current implementation status

The code now includes a PostgreSQL-backed idempotency reservation, but the storage dependency is not configured or validated in Render, the migration has not been applied to an active database, CI results are not yet confirmed, and no end-to-end dispatch has been performed. The operations feature remains WIP and disabled by default.
