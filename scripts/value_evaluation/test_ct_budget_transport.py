"""Synthetic offline tests. Never instantiate a real network opener."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.request import Request

from budget_guard import BudgetRefused, Ledger, RunConfig
from ct_budget_transport import BudgetedOpener, ChargeReservation, ENDPOINT, MODEL


class FakeOpener:
    def __init__(self):
        self.calls = []
        self.result = object()
        self.error = None

    def open(self, request, *, timeout):
        self.calls.append((request, timeout))
        if self.error:
            raise self.error
        return self.result


class TransportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ct-budget-transport-")
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "ledger.json"
        self.config = RunConfig("synthetic", "a" * 64, "25", "2",
                                "verified_upper_bound", "synthetic-approval")
        self.ledger = Ledger.create(self.path, self.config)
        self.delegate = FakeOpener()

    def request(self, url=ENDPOINT, model=MODEL, method="POST"):
        body = json.dumps({"model": model, "max_tokens": 32768,
                           "messages": [{"role": "user", "content": "synthetic"}]}).encode()
        return Request(url, body, {"x-api-key": "FAKE-NOT-A-CREDENTIAL",
                                  "Content-Type": "application/json",
                                  "Anthropic-Version": "2023-06-01"}, method=method)

    def wrapper(self, call="call", amount="1", resolver=None):
        return BudgetedOpener(self.delegate, self.ledger, call, "packet",
                              resolver or (lambda _: ChargeReservation(amount, "synthetic-bound", "verified_upper_bound")))

    def state(self):
        return json.loads(self.path.read_text())

    def test_detached_request_exact_bytes_headers_timeout_and_response(self):
        request = self.request()
        data, headers = request.data, tuple(request.header_items())
        seen = []
        def resolver(snapshot):
            seen.append(snapshot)
            return ChargeReservation("1", "synthetic-bound", "verified_upper_bound")
        result = self.wrapper(resolver=resolver).open(request, timeout=90)
        self.assertIs(result, self.delegate.result)
        self.assertIsNot(self.delegate.calls[0][0], request)
        self.assertEqual(self.delegate.calls[0][0].data, data)
        self.assertEqual(tuple(self.delegate.calls[0][0].header_items()), headers)
        self.assertEqual(self.delegate.calls[0][1], 90)
        self.assertIs(request.data, data)
        self.assertEqual(tuple(request.header_items()), headers)
        self.assertEqual(seen[0].body, data)
        self.assertNotIn("FAKE-NOT-A-CREDENTIAL", repr(seen))
        self.assertIsNone(self.state()["calls"]["call"]["actual_usd"])

    def test_reservation_precedes_send(self):
        owner = self
        class CheckingOpener:
            def open(self, request, *, timeout):
                owner.assertIn("call", owner.state()["calls"])
        self.delegate = CheckingOpener()
        self.wrapper().open(self.request(), timeout=90)

    def test_ledger_refusal_prevents_send(self):
        with self.assertRaises(BudgetRefused):
            self.wrapper(amount="2.000001").open(self.request(), timeout=90)
        self.assertEqual(self.delegate.calls, [])

    def test_unknown_outcome_retains_reservation_and_never_retries(self):
        self.delegate.error = TimeoutError("synthetic")
        with self.assertRaises(TimeoutError):
            self.wrapper(amount="2").open(self.request(), timeout=90)
        call = self.state()["calls"]["call"]
        self.assertEqual(call["reserved_usd"], "2")
        self.assertIsNone(call["actual_usd"])
        self.assertEqual(len(self.delegate.calls), 1)

    def test_two_requests_same_packet_aggregate_cap(self):
        self.wrapper("one", "1.5").open(self.request(), timeout=90)
        with self.assertRaises(BudgetRefused):
            self.wrapper("two", "0.500001").open(self.request(), timeout=90)
        self.assertEqual(len(self.delegate.calls), 1)

    def test_duplicate_wrapper_cannot_dispatch_twice(self):
        wrapper = self.wrapper()
        wrapper.open(self.request(), timeout=90)
        with self.assertRaises(BudgetRefused):
            wrapper.open(self.request(), timeout=90)
        self.assertEqual(len(self.delegate.calls), 1)

    def test_absent_bound_prevents_send(self):
        with self.assertRaises(BudgetRefused):
            self.wrapper(resolver=lambda _: None).open(self.request(), timeout=90)
        self.assertEqual(self.state()["calls"], {})
        self.assertEqual(self.delegate.calls, [])
        with self.assertRaises(BudgetRefused):
            BudgetedOpener(self.delegate, self.ledger, "x", "packet", None)

    def test_allowlist_refuses_other_endpoint_method_model(self):
        for request in (self.request(url=ENDPOINT + "?x=1"),
                        self.request(url="http://api.anthropic.com/v1/messages"),
                        self.request(model="different-model"), self.request(method="GET")):
            with self.subTest(url=request.full_url), self.assertRaises(BudgetRefused):
                self.wrapper().open(request, timeout=90)
        self.assertEqual(self.delegate.calls, [])

    def test_duplicate_json_and_unbounded_output_refused(self):
        for body in (b'{"model":"x","model":"claude-sonnet-5-5","max_tokens":10}',
                     b'{"model":"claude-sonnet-5-5","max_tokens":32769}'):
            with self.assertRaises(BudgetRefused):
                request = self.request()
                request.data = body
                self.wrapper().open(request, timeout=90)
        self.assertEqual(self.delegate.calls, [])

    def test_mutating_original_during_resolution_cannot_change_dispatch(self):
        request = self.request()
        original_data = request.data
        def resolver(_):
            request.add_header("changed", "yes")
            request.data = b"changed"
            return ChargeReservation("1", "synthetic-bound", "verified_upper_bound")
        self.wrapper(resolver=resolver).open(request, timeout=90)
        sent = self.delegate.calls[0][0]
        self.assertEqual(sent.data, original_data)
        self.assertNotIn("Changed", dict(sent.header_items()))

    def test_header_modifiers_rejected_before_resolver_or_send(self):
        seen = []
        for name, value in (("anthropic-beta", "synthetic-beta"),
                            ("x-route", "synthetic-route"),
                            ("Content-Type", "text/plain"),
                            ("Anthropic-Version", "2024-01-01")):
            request = self.request()
            request.add_header(name, value)
            with self.subTest(name=name), self.assertRaises(BudgetRefused):
                self.wrapper(resolver=lambda snapshot: seen.append(snapshot)).open(request, timeout=90)
        self.assertEqual(seen, [])
        self.assertEqual(self.delegate.calls, [])
        self.assertEqual(self.state()["calls"], {})

    def test_missing_and_shadowed_headers_rejected(self):
        for action in (lambda request: request.remove_header("Anthropic-version"),
                       lambda request: request.add_unredirected_header("Anthropic-Version", "2023-06-01")):
            request = self.request()
            action(request)
            with self.assertRaises(BudgetRefused):
                self.wrapper().open(request, timeout=90)
        self.assertEqual(self.delegate.calls, [])

    def test_mutating_original_during_reservation_cannot_change_dispatch(self):
        request = self.request()
        original = request.data
        reserve = self.ledger.reserve
        def mutate(*args):
            request.full_url = "https://example.invalid/"
            request.data = b"changed"
            request.add_header("anthropic-beta", "changed")
            return reserve(*args)
        with patch.object(self.ledger, "reserve", side_effect=mutate):
            self.wrapper().open(request, timeout=90)
        sent = self.delegate.calls[0][0]
        self.assertEqual(sent.full_url, ENDPOINT)
        self.assertEqual(sent.data, original)
        self.assertNotIn("Anthropic-beta", dict(sent.header_items()))

    def test_policy_mismatch_does_not_dispatch(self):
        resolver = lambda _: ChargeReservation("1", "reviewed-estimate", "approved_conservative_estimate")
        with self.assertRaises(BudgetRefused):
            self.wrapper(resolver=resolver).open(self.request(), timeout=90)
        self.assertEqual(self.delegate.calls, [])
        self.assertEqual(self.state()["calls"], {})

    def test_failed_reservation_write_does_not_dispatch(self):
        with patch("budget_guard.os.replace", side_effect=OSError("synthetic")):
            with self.assertRaises(OSError):
                self.wrapper().open(self.request(), timeout=90)
        self.assertEqual(self.delegate.calls, [])
        self.assertEqual(self.state()["calls"], {})


if __name__ == "__main__":
    unittest.main()
