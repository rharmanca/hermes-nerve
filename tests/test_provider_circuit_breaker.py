import unittest, time
import hermes_nerve.client as client
from hermes_nerve.client import JevClient, JevError

def _failing(url, headers, body, timeout):
    raise JevError("Jev API connection failed: simulated")

def _ok(url, headers, body, timeout):
    return 200, b'{"answers": {"q": {"value": "x"}}, "model": "m"}', {}

class CircuitBreakerTests(unittest.TestCase):
    def setUp(self):
        client.reset_breaker()
        self.addCleanup(client.reset_breaker)

    def _client(self, transport):
        return JevClient(provider="typesafe", api_key="k", base_url="https://example.invalid", timeout=1.0, transport=transport)

    def test_opens_after_consecutive_connection_failures_and_fails_fast(self):
        c = self._client(_failing); calls = []
        orig = c._transport
        c._transport = lambda *a: (calls.append(1), orig(*a))[1]
        for _ in range(client.BREAKER_THRESHOLD):
            with self.assertRaises(JevError): c.system_one(state={}, questions={"q": {}})
        self.assertEqual(len(calls), client.BREAKER_THRESHOLD)
        with self.assertRaisesRegex(JevError, "circuit open"): c.system_one(state={}, questions={"q": {}})
        self.assertEqual(len(calls), client.BREAKER_THRESHOLD, "open circuit must not hit the transport")
        self.assertTrue(client.breaker_state()["open"])

    def test_success_resets_failure_count(self):
        bad = self._client(_failing); good = self._client(_ok)
        with self.assertRaises(JevError): bad.system_one(state={}, questions={"q": {}})
        good.system_one(state={}, questions={"q": {}})
        self.assertEqual(client.breaker_state()["failures"], 0)
        self.assertFalse(client.breaker_state()["open"])

    def test_http_errors_do_not_trip_breaker(self):
        def http402(url, headers, body, timeout): return 402, b"no credits", {}
        c = self._client(http402)
        for _ in range(client.BREAKER_THRESHOLD + 1):
            with self.assertRaisesRegex(JevError, "HTTP 402"): c.system_one(state={}, questions={"q": {}})
        self.assertFalse(client.breaker_state()["open"])

    def test_cooldown_expires(self):
        old = client.BREAKER_COOLDOWN; client.BREAKER_COOLDOWN = 0.05
        self.addCleanup(lambda: setattr(client, "BREAKER_COOLDOWN", old))
        c = self._client(_failing)
        for _ in range(client.BREAKER_THRESHOLD):
            with self.assertRaises(JevError): c.system_one(state={}, questions={"q": {}})
        self.assertTrue(client.breaker_state()["open"]); time.sleep(0.06)
        self.assertFalse(client.breaker_state()["open"])

if __name__ == "__main__": unittest.main()
