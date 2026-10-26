from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.ops import DispatchRequest, _auth, _ensure_dispatch_enabled, _validate


class DispatchValidationTests(unittest.TestCase):
    def test_dry_run_defaults_to_true(self):
        request = DispatchRequest(repo="lite", operation="gate")
        self.assertTrue(request.dry_run)
        _validate(request)

    def test_rejects_arbitrary_repository(self):
        with self.assertRaises(ValueError):
            DispatchRequest(repo="attacker/repo", operation="gate")

    def test_docs_publish_requires_integrity_fields(self):
        request = DispatchRequest(repo="lite", operation="docs", dry_run=False)
        with self.assertRaises(HTTPException) as raised:
            _validate(request)
        self.assertEqual(raised.exception.status_code, 422)

    def test_docs_rejects_invalid_hash(self):
        request = DispatchRequest(
            repo="lite", operation="docs", docs_patch_sha256="not-a-hash"
        )
        with self.assertRaises(HTTPException) as raised:
            _validate(request)
        self.assertEqual(raised.exception.status_code, 422)

    def test_gate_rejects_document_patch_fields(self):
        request = DispatchRequest(
            repo="lite", operation="gate", docs_patch_url="https://example.invalid/patch"
        )
        with self.assertRaises(HTTPException) as raised:
            _validate(request)
        self.assertEqual(raised.exception.status_code, 422)

    def test_dispatch_is_disabled_by_default(self):
        with patch.dict(os.environ, {"OPS_DISPATCH_ENABLED": ""}, clear=False):
            with self.assertRaises(HTTPException) as raised:
                _ensure_dispatch_enabled()
        self.assertEqual(raised.exception.status_code, 503)

    def test_dispatch_can_only_be_enabled_explicitly(self):
        with patch.dict(os.environ, {"OPS_DISPATCH_ENABLED": "true"}, clear=False):
            _ensure_dispatch_enabled()

    def test_auth_requires_configured_key(self):
        with patch.dict(os.environ, {"OPS_API_KEY": ""}, clear=False):
            with self.assertRaises(HTTPException) as raised:
                _auth("anything")
        self.assertEqual(raised.exception.status_code, 503)

    def test_auth_rejects_wrong_key(self):
        with patch.dict(os.environ, {"OPS_API_KEY": "expected-test-key"}, clear=False):
            with self.assertRaises(HTTPException) as raised:
                _auth("wrong-test-key")
        self.assertEqual(raised.exception.status_code, 401)

    def test_auth_accepts_correct_key(self):
        with patch.dict(os.environ, {"OPS_API_KEY": "expected-test-key"}, clear=False):
            _auth("expected-test-key")


if __name__ == "__main__":
    unittest.main()
