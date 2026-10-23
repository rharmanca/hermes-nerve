"""Provider failure handling: every transport-level failure must be wrapped as a
JevError connection failure, count toward the circuit breaker and classify into a
stable privacy-safe error category."""
import http.client
import io
import ssl
import unittest
import urllib.error
import urllib.request

import hermes_nerve.client as client
from hermes_nerve.client import JevClient, JevConnectionError, JevError, error_category


def _ok(url, headers, body, timeout):
    return 200, b'{"answers": {"q": {"value": "x"}}, "model": "m"}', {}


class _FakeResponse:
    status = 200
    headers = {}

    def __init__(self, read_exc):
        self._read_exc = read_exc

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        raise self._read_exc


class RawTransportFailureTests(unittest.TestCase):
    def setUp(self):
        client.reset_breaker()
        self.addCleanup(client.reset_breaker)

    def _client(self, transport):
        return JevClient(provider="typesafe", api_key="k", base_url="https://example.invalid", timeout=1.0, transport=transport)

    def test_raw_oserror_from_transport_is_connection_failure_and_trips_breaker(self):
        def reset(url, headers, body, timeout):
            raise ConnectionResetError(104, "Connection reset by peer")
        c = self._client(reset)
        for _ in range(client.BREAKER_THRESHOLD):
            with self.assertRaises(JevConnectionError) as ctx:
                c.system_one(state={}, questions={"q": {}})
            self.assertIn("connection failed", str(ctx.exception))
        self.assertTrue(client.breaker_state()["open"], "raw socket errors must count toward the breaker")

    def test_raw_timeout_from_transport_is_connection_failure(self):
        def slow(url, headers, body, timeout):
            raise TimeoutError("timed out")
        c = self._client(slow)
        with self.assertRaises(JevConnectionError):
            c.system_one(state={}, questions={"q": {}})
        self.assertEqual(client.breaker_state()["failures"], 1)

    def test_http_exception_from_transport_is_connection_failure(self):
        def truncated(url, headers, body, timeout):
            raise http.client.IncompleteRead(b"partial")
        c = self._client(truncated)
        with self.assertRaises(JevConnectionError):
            c.system_one(state={}, questions={"q": {}})
        self.assertEqual(client.breaker_state()["failures"], 1)

    def test_unexpected_transport_exception_is_wrapped_but_does_not_trip_breaker(self):
        def broken(url, headers, body, timeout):
            raise ValueError("transport bug")
        c = self._client(broken)
        with self.assertRaises(JevError) as ctx:
            c.system_one(state={}, questions={"q": {}})
        self.assertNotIsInstance(ctx.exception, JevConnectionError)
        self.assertEqual(client.breaker_state()["failures"], 0, "programming errors are not provider outages")

    def test_open_breaker_raises_connection_error_subclass(self):
        def reset(url, headers, body, timeout):
            raise ConnectionResetError("boom")
        c = self._client(reset)
        for _ in range(client.BREAKER_THRESHOLD):
            with self.assertRaises(JevError):
                c.system_one(state={}, questions={"q": {}})
        with self.assertRaises(JevConnectionError) as ctx:
            c.system_one(state={}, questions={"q": {}})
        self.assertIn("circuit open", str(ctx.exception))


class UrllibTransportReadFailureTests(unittest.TestCase):
    """Failures raised while reading the body happen inside the ``with`` block and are
    not URLError instances; they must still be classified as connection failures."""

    def setUp(self):
        client.reset_breaker()
        self.addCleanup(client.reset_breaker)
        self._orig = urllib.request.urlopen
        self.addCleanup(setattr, urllib.request, "urlopen", self._orig)

    def _client(self):
        return JevClient(provider="typesafe", api_key="k", base_url="https://example.invalid", timeout=1.0)

    def _patch_read(self, exc):
        urllib.request.urlopen = lambda request, timeout=None: _FakeResponse(exc)

    def test_incomplete_read(self):
        self._patch_read(http.client.IncompleteRead(b"x"))
        with self.assertRaises(JevConnectionError):
            self._client().system_one(state={}, questions={"q": {}})
        self.assertEqual(client.breaker_state()["failures"], 1)

    def test_read_timeout(self):
        self._patch_read(TimeoutError("The read operation timed out"))
        with self.assertRaises(JevConnectionError):
            self._client().system_one(state={}, questions={"q": {}})
        self.assertEqual(client.breaker_state()["failures"], 1)

    def test_ssl_error_during_read(self):
        self._patch_read(ssl.SSLError(1, "[SSL: UNEXPECTED_EOF_WHILE_READING]"))
        with self.assertRaises(JevConnectionError):
            self._client().system_one(state={}, questions={"q": {}})
        self.assertEqual(client.breaker_state()["failures"], 1)

    def test_connection_reset_during_read(self):
        self._patch_read(ConnectionResetError("reset"))
        with self.assertRaises(JevConnectionError):
            self._client().system_one(state={}, questions={"q": {}})
        self.assertEqual(client.breaker_state()["failures"], 1)


class ErrorCategoryTests(unittest.TestCase):
    """Categories are stable, short, and never include provider/response text."""

    def test_categories(self):
        cases = [
            (JevConnectionError("Jev provider circuit open after 2 consecutive connection failures; retrying in 59s"), "circuit-open"),
            (JevConnectionError("Jev API connection failed: The read operation timed out"), "timeout"),
            (JevConnectionError("Jev API connection failed: <urlopen error timed out>"), "timeout"),
            (JevConnectionError("Jev API connection failed: <urlopen error _ssl.c:1000: The handshake operation timed out>"), "timeout"),
            (JevConnectionError("Jev API connection failed: <urlopen error [SSL: UNEXPECTED_EOF] ...>"), "connection"),
            (JevConnectionError("Jev API connection failed: [Errno 104] Connection reset by peer"), "connection"),
            (JevError("Jev API returned HTTP 402: no credits"), "http-402"),
            (JevError("Jev API returned HTTP 503: <html>upstream</html>"), "http-503"),
            (JevError("Jev API returned invalid JSON"), "invalid-response"),
            (JevError("Jev transport returned an invalid response tuple"), "invalid-response"),
            (JevError("Jev API response is missing answers"), "invalid-response"),
            (JevError("No Jev provider API key found"), "no-provider"),
            (JevError("Jev API transport failed: ValueError: transport bug"), "transport-error"),
            (RuntimeError("something else entirely with secret text"), "other"),
            (ValueError("x"), "other"),
        ]
        for exc, expected in cases:
            with self.subTest(exc=str(exc)[:60]):
                self.assertEqual(error_category(exc), expected)

    def test_category_never_leaks_message_text(self):
        exc = JevError("Jev API returned HTTP 500: Bearer sk-live-leak and user@example.com")
        category = error_category(exc)
        self.assertNotIn("sk-live", category)
        self.assertNotIn("@", category)
        self.assertLessEqual(len(category), 24)


if __name__ == "__main__":
    unittest.main()
