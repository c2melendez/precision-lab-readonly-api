"""Durable idempotency for operational dispatch.

Requires an explicitly provisioned PostgreSQL database and the migration in
migrations/001_ops_idempotency.sql. Missing configuration/schema fails closed.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row


class IdempotencyUnavailable(RuntimeError):
    """Durable idempotency storage is not configured or unavailable."""


class IdempotencyConflict(RuntimeError):
    """A key was reused for a different canonical request."""


def _database_url() -> str:
    value = os.getenv("OPS_DATABASE_URL", "").strip()
    if not value:
        raise IdempotencyUnavailable("Operations idempotency storage is not configured")
    return value


def _connect():
    try:
        return psycopg.connect(_database_url(), connect_timeout=3, row_factory=dict_row)
    except Exception as exc:
        raise IdempotencyUnavailable("Operations idempotency storage is unavailable") from exc


def digest_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def digest_request(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def reserve(
    *,
    key_sha256: str,
    request_sha256: str,
    repo: str,
    operation: str,
    dry_run: bool,
) -> dict[str, Any]:
    """Atomically reserve a key; an existing key can never trigger a second dispatch."""
    try:
        with _connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO ops_idempotency
                        (key_sha256, request_sha256, repo, operation, dry_run, status)
                    VALUES (%s, %s, %s, %s, %s, 'dispatching')
                    ON CONFLICT (key_sha256) DO NOTHING
                    RETURNING operation_id, request_sha256, status
                    """,
                    (key_sha256, request_sha256, repo, operation, dry_run),
                )
                row = cursor.fetchone()
                created = row is not None
                if row is None:
                    cursor.execute(
                        """
                        SELECT operation_id, request_sha256, status
                        FROM ops_idempotency
                        WHERE key_sha256 = %s
                        """,
                        (key_sha256,),
                    )
                    row = cursor.fetchone()
                if row is None:
                    raise IdempotencyUnavailable("Could not reserve idempotency key")
                if row["request_sha256"].strip() != request_sha256:
                    raise IdempotencyConflict("Idempotency key was already used for a different request")
                return {
                    "operation_id": str(row["operation_id"]),
                    "status": row["status"],
                    "created": created,
                }
    except (IdempotencyConflict, IdempotencyUnavailable):
        raise
    except Exception as exc:
        raise IdempotencyUnavailable("Operations idempotency storage is unavailable") from exc


def set_status(operation_id: str, status: str) -> None:
    if status not in {"accepted", "rejected"}:
        raise ValueError("Unsupported terminal idempotency status")
    try:
        UUID(operation_id)
        with _connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE ops_idempotency
                    SET status = %s, updated_at = now()
                    WHERE operation_id = %s AND status = 'dispatching'
                    """,
                    (status, operation_id),
                )
                if cursor.rowcount != 1:
                    raise IdempotencyUnavailable("Idempotency operation state could not be updated")
    except IdempotencyUnavailable:
        raise
    except Exception as exc:
        raise IdempotencyUnavailable("Operations idempotency storage is unavailable") from exc
