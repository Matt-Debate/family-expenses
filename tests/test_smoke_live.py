"""Live-gate helpers: pooled Neon URI and prepared-statement repetition."""

from __future__ import annotations

import unittest

from scripts.smoke_live import exercise_pooled_token_gate, is_neon_pooled_url


class _TokenStore:
    def __init__(self):
        self.tokens: list[str] = []

    def validate_token(self, token):
        self.tokens.append(token)
        return {"token": token}


class PooledDatabaseGateTests(unittest.TestCase):
    def test_neon_pooler_hostname_is_required(self):
        self.assertTrue(is_neon_pooled_url(
            "postgresql://example@ep-family-pooler.ap-southeast-1.aws.neon.tech/db"
        ))
        self.assertFalse(is_neon_pooled_url(
            "postgresql://example@ep-family.ap-southeast-1.aws.neon.tech/db"
        ))

    def test_token_query_shape_repeats_at_least_six_times(self):
        store = _TokenStore()
        self.assertTrue(exercise_pooled_token_gate(store, "smoke-token"))
        self.assertGreaterEqual(len(store.tokens), 6)
        self.assertEqual(set(store.tokens), {"smoke-token"})


if __name__ == "__main__":
    unittest.main()
