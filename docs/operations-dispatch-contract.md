# Operational dispatch contract (proposal)

Status: design contract only; no operational endpoint is deployed.

## Trust boundaries

- The public certification API remains read-only.
- A separate operations router, if approved and implemented, must require `X-Ops-Key`; it must not reuse `X-API-Key`.
- The router may request only fixed operations against the two allowlisted repositories: `c2melendez/precision-lab-lite` and `c2melendez/precision-lab-plus`.
- The runner, not the API or the language model, performs repository mutations.
- GitHub credentials must be stored as server/Actions secrets and must never be returned, logged, or included in prompts.
- No arbitrary workflow names, refs, repository names, shell commands, or file paths may be accepted from a caller.

## Initial operation allowlist

| Operation | Purpose | Default |
|---|---|---|
| `gate` | Request a gate already declared in certification continuity | `dry_run=true` |
| `docs` | Validate a documentation patch against the runner's strict path policy | `dry_run=true` |

A code-edit operation is intentionally not part of this first contract. It requires a separate review of patch boundaries, path policy, tests, concurrency and compare-and-swap behavior before it can be enabled.

## Request validation

- `repo` is exactly `lite` or `plus`.
- `operation` is exactly `gate` or `docs`.
- `gate` is selected from the runner's fixed allowlist.
- Documentation publication requires a 64-character lowercase SHA-256, a full 40-character expected base commit SHA, and an HTTPS patch URL.
- Patch host restrictions must be enforced by the runner; URL syntax validation in an API is not sufficient.
- A non-dry-run request must be auditable and idempotent. Repeated equivalent requests must return or reference the existing operation instead of dispatching duplicate work.

## Dispatch semantics

1. Authenticate the operations caller with constant-time key comparison.
2. Validate the entire request before contacting GitHub.
3. Dispatch only the fixed runner workflow and a fixed trusted ref.
4. Return `202 Accepted` only when GitHub confirms the dispatch request was accepted.
5. State clearly that acceptance is not evidence of completion or certification PASS.
6. Record a correlation/idempotency key without storing secrets.
7. Provide read-only status/evidence lookup separately; do not expose a caller-supplied arbitrary run URL.

## Rollout gates

- Unit tests for authentication, malformed input, allowlists, dry-run behavior and idempotency.
- Tests proving that invalid requests make no GitHub network call.
- Review token permissions and Render environment variable names without reading or returning secret values.
- Verify the live health endpoint and authenticated read-only endpoints before enabling operations.
- Keep operational dispatch disabled until a dedicated operations credential and least-privilege dispatch token are configured.
- Do not trigger product gates just to test transport. Use dry-run validation first.

## Current implementation status

The existing deployed API remains read-only. This document does not claim that an operational route, dispatch token, or end-to-end dispatch has been implemented or tested.
