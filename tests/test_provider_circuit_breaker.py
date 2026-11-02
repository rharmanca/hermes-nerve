"""Judge-outage fast-fail: the client circuit breaker (rob fork port of 9d18cc6+719a8a1)."""
from __future__ import annotations

import socket
import time
import unittest
import urllib.error

from hermes_nerve import client
from hermes_nerve.client import JevClient, JevConnectionError, JevError

OK = (200, b'{"answers": {"q": {"value": true}}}', {})


class Transport:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self, url, headers, body, timeout):
        self.calls += 1
        outcome = self.outcomes.pop(0) if self.outcomes else OK
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def make(transport):
    return JevClient(provider="typesafe", api_key="k", transport=transport)


Q = {"q": {"type": "boolean", "question": "ok?"}}


class BreakerTests(unittest.TestCase):
    def setUp(self):
        client.reset_breaker()
        self._t, self._c = client.BREAKER_THRESHOLD, client.BREAKER_COOLDOWN
        client.configure(breaker_threshold=2, breaker_cooldown=60)

    def tearDown(self):
        client.BREAKER_THRESHOLD, client.BREAKER_COOLDOWN = self._t, self._c
        client.reset_breaker()

    def test_opens_after_threshold_and_fails_fast_without_calling_transport(self):
        t = Transport(JevConnectionError("Jev API connection failed: timed out"),
                      JevConnectionError("Jev API connection failed: timed out"))
        c = make(t)
        for _ in range(2):
            with self.assertRaises(JevConnectionError):
                c.system_one(state={}, questions=Q)
        self.assertTrue(client.breaker_state()["open"])
        started = time.monotonic()
        with self.assertRaises(JevConnectionError) as ctx:
            c.system_one(state={}, questions=Q)
        self.assertIn("circuit open", str(ctx.exception))
        self.assertEqual(t.calls, 2, "open breaker must not call the provider")
        self.assertLess(time.monotonic() - started, 0.5)

    def test_raw_socket_failures_count_as_outages(self):
        t = Transport(socket.timeout("timed out"), urllib.error.URLError("refused"))
        c = make(t)
        for _ in range(2):
            with self.assertRaises(JevConnectionError):
                c.system_one(state={}, questions=Q)
        self.assertTrue(client.breaker_state()["open"])

    def test_http_errors_do_not_trip_breaker(self):
        t = Transport((500, b"boom", {}), (503, b"boom", {}), (502, b"x", {}))
        c = make(t)
        for _ in range(3):
            with self.assertRaises(JevError) as ctx:
                c.system_one(state={}, questions=Q)
            self.assertNotIsInstance(ctx.exception, JevConnectionError)
        self.assertFalse(client.breaker_state()["open"])
        self.assertEqual(t.calls, 3)

    def test_success_resets_failure_count(self):
        t = Transport(JevConnectionError("Jev API connection failed: x"), OK,
                      JevConnectionError("Jev API connection failed: x"))
        c = make(t)
        with self.assertRaises(JevConnectionError):
            c.system_one(state={}, questions=Q)
        c.system_one(state={}, questions=Q)
        with self.assertRaises(JevConnectionError):
            c.system_one(state={}, questions=Q)
        self.assertFalse(client.breaker_state()["open"])

    def test_recovers_after_cooldown(self):
        client.configure(breaker_cooldown=0)
        t = Transport(JevConnectionError("Jev API connection failed: x"),
                      JevConnectionError("Jev API connection failed: x"))
        c = make(t)
        for _ in range(2):
            with self.assertRaises(JevConnectionError):
                c.system_one(state={}, questions=Q)
        resp = c.system_one(state={}, questions=Q)
        self.assertIn("q", resp.answers)
        self.assertEqual(client.breaker_state()["failures"], 0)

    def test_open_breaker_error_is_a_jeverror_so_existing_outage_handling_applies(self):
        self.assertTrue(issubclass(JevConnectionError, JevError))


if __name__ == "__main__":
    unittest.main()
