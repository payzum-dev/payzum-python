"""Test suite, a port of payzum-php's tests/run.php against the same shared
webhook-signature corpus. Runs with nothing but the standard library:

    python -m unittest discover -s tests
"""

from __future__ import annotations

import hashlib
import json
import unittest
from decimal import Decimal
from pathlib import Path

from payzum import (
    ERROR_CODES,
    IPN_EVENT_TYPES,
    ApiError,
    Client,
    Config,
    Payzum,
    PayzumError,
    PaymentStatus,
    Response,
    SignatureError,
    TransportError,
    Verifier,
    decode_lossless,
    is_retryable_code,
)
from payzum.client import CONSISTENCY_WINDOW_SECONDS

FIXTURE = Path(__file__).parent / "fixtures" / "webhook-signatures.json"
UPSTREAM = Path(__file__).parent / "../../payzum-openapi/vectors/webhook-signatures.json"

CORPUS = json.loads(FIXTURE.read_text())
NOW = 1788000000
KEY = "a" * 64


class FakeTransport:
    """Scripted transport: returns queued responses, records every call."""

    def __init__(self, queue):
        self.queue = list(queue)
        self.calls = []

    def send(self, method, url, headers, query, body, timeout_seconds):
        self.calls.append(
            {"method": method, "url": url, "headers": dict(headers), "query": dict(query), "body": body}
        )
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def json_response(status, body, headers=None):
    return Response(status, json.dumps(body), headers or {})


def error_response(status, code, headers=None):
    return json_response(status, {"statusCode": status, "code": code, "message": "x"}, headers)


def make_client(queue, slept=None):
    transport = FakeTransport(queue)
    sleeper = (lambda s: slept.append(s)) if slept is not None else (lambda s: None)
    return Client(Config(KEY), transport, sleeper), transport


class TestWebhookCorpus(unittest.TestCase):
    """Every SDK verifies the exact same vectors, computed from the gateway."""

    def test_fixture_matches_upstream_when_checked_out(self):
        if not UPSTREAM.exists():
            self.skipTest("payzum-openapi not checked out")
        self.assertEqual(
            hashlib.sha256(UPSTREAM.read_bytes()).hexdigest(),
            hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
            "copy is stale — refresh it from the payzum-openapi repo",
        )

    def test_all_vectors(self):
        verifier = Verifier(CORPUS["secret"], CORPUS["replay_window_seconds"])
        methods = {
            "payment_ipn": (verifier.verify_payment_ipn, "x-nowpayments-sig"),
            "coinpayments_ipn": (verifier.verify_coinpayments_ipn, "HMAC"),
            "mass_payout": (verifier.verify_mass_payout, "X-Payzum-Signature"),
        }

        for scheme, spec in CORPUS["schemes"].items():
            method, header = methods[scheme]

            for case in spec["cases"]:
                with self.subTest(scheme=scheme, case=case["name"]):
                    headers = {header: case["signature"]}
                    try:
                        if scheme == "coinpayments_ipn":
                            method(case["body"], headers)
                        else:
                            method(case["body"], headers, NOW)
                        accepted = True
                    except SignatureError:
                        accepted = False
                    self.assertEqual(accepted, case["valid"])

            for case in spec.get("replay_cases", []):
                with self.subTest(scheme=scheme, replay=case["name"]):
                    headers = {header: case["signature"]}
                    try:
                        method(case["body"], headers, case["now"])
                        accepted = True
                    except SignatureError:
                        accepted = False
                    self.assertEqual(accepted, case["accept"])


class TestCrossSchemeConfusion(unittest.TestCase):
    """The bug that hit 20 of 21 plugins."""

    def setUp(self):
        self.verifier = Verifier(CORPUS["secret"], CORPUS["replay_window_seconds"])
        self.ipn = CORPUS["schemes"]["payment_ipn"]["cases"][0]

    def test_mass_payout_header_rejected_for_payment_ipn(self):
        with self.assertRaises(SignatureError) as ctx:
            self.verifier.verify_payment_ipn(
                self.ipn["body"], {"X-Payzum-Signature": self.ipn["signature"]}, NOW
            )
        self.assertEqual(ctx.exception.reason, SignatureError.REASON_MISSING_HEADER)

    def test_sha256_signature_rejected_by_sha512_verifier(self):
        mp = CORPUS["schemes"]["mass_payout"]["cases"][0]
        with self.assertRaises(SignatureError):
            self.verifier.verify_payment_ipn(
                mp["body"], {"x-nowpayments-sig": mp["signature"]}, NOW
            )


class TestHeaderLookup(unittest.TestCase):
    def setUp(self):
        self.verifier = Verifier(CORPUS["secret"], CORPUS["replay_window_seconds"])
        self.ipn = CORPUS["schemes"]["payment_ipn"]["cases"][0]

    def test_case_and_format_variants_accepted(self):
        for variant in ("X-NowPayments-Sig", "x-nowpayments-sig", "HTTP_X_NOWPAYMENTS_SIG"):
            with self.subTest(variant=variant):
                self.verifier.verify_payment_ipn(
                    self.ipn["body"], {variant: self.ipn["signature"]}, NOW
                )

    def test_event_id_read_case_insensitively(self):
        self.assertEqual(
            self.verifier.event_id({"X-Payzum-Event-Id": "pzie_abc"}), "pzie_abc"
        )

    def test_all_five_ipn_event_types_are_typed(self):
        self.assertEqual(len(IPN_EVENT_TYPES), 5)
        self.assertIn("wrong_token_received", IPN_EVENT_TYPES)
        self.assertIn("suspicious_token_received", IPN_EVENT_TYPES)
        self.assertNotIn("invoice.partial", IPN_EVENT_TYPES)


class TestDecimals(unittest.TestCase):
    """Money keeps its digits."""

    def test_18_decimals_survive_decoding(self):
        exact = "0.123456789012345678"
        decoded = decode_lossless('{"pay_amount":' + exact + "}")
        self.assertEqual(decoded["pay_amount"], Decimal(exact))
        self.assertEqual(format(decoded["pay_amount"], "f"), exact)

    def test_float_would_have_lost_them(self):
        exact = "0.123456789012345678"
        self.assertNotEqual(repr(float(exact)), exact)

    def test_digits_inside_strings_untouched(self):
        tricky = decode_lossless('{"order_id":"ORDER-12345","desc":"say \\"42\\" now","amt":1.5}')
        self.assertEqual(tricky["order_id"], "ORDER-12345")
        self.assertEqual(tricky["desc"], 'say "42" now')
        self.assertEqual(tricky["amt"], Decimal("1.5"))


class TestStatusMapping(unittest.TestCase):
    def test_mappings(self):
        self.assertIs(PaymentStatus.from_merchant("finished"), PaymentStatus.FINISHED)
        self.assertIs(PaymentStatus.from_buyer("paid"), PaymentStatus.FINISHED)
        self.assertIs(PaymentStatus.from_buyer("overpaid"), PaymentStatus.FINISHED)
        self.assertIs(PaymentStatus.from_buyer("cancelled"), PaymentStatus.FAILED)
        self.assertIs(PaymentStatus.from_buyer("pending"), PaymentStatus.WAITING)

    def test_terminality_and_paid(self):
        self.assertTrue(PaymentStatus.FINISHED.is_terminal())
        self.assertFalse(PaymentStatus.WAITING.is_terminal())
        self.assertTrue(PaymentStatus.FINISHED.is_paid())
        self.assertFalse(PaymentStatus.PARTIALLY_PAID.is_paid())

    def test_unconfirmed_rejected(self):
        with self.assertRaises(PayzumError):
            PaymentStatus.from_merchant("unconfirmed")


class TestErrorCodes(unittest.TestCase):
    def test_sixteen_codes_defined(self):
        self.assertEqual(len(ERROR_CODES), 16)

    def test_exactly_three_retryable(self):
        retryable = [c for c in ERROR_CODES if is_retryable_code(c)]
        self.assertEqual(
            sorted(retryable),
            sorted(["RATE_LIMIT_EXCEEDED", "INTERNAL_ERROR", "RATE_PROVIDER_DOWN"]),
        )

    def test_quota_exceeded_not_retryable_despite_429(self):
        self.assertFalse(is_retryable_code("QUOTA_EXCEEDED"))
        self.assertFalse(is_retryable_code("PAYMENT_NOT_FOUND"))


class TestClientBehaviour(unittest.TestCase):
    """Retries, idempotency, error mapping."""

    def test_retries_internal_error_then_succeeds(self):
        client, transport = make_client(
            [error_response(500, "INTERNAL_ERROR"), json_response(200, {"ok": True})]
        )
        client.request("GET", "/v1/payment")
        self.assertEqual(len(transport.calls), 2)

    def test_quota_exceeded_not_retried(self):
        client, transport = make_client([error_response(429, "QUOTA_EXCEEDED")])
        with self.assertRaises(ApiError) as ctx:
            client.request("GET", "/v1/payment")
        self.assertEqual(len(transport.calls), 1)
        self.assertFalse(ctx.exception.is_retryable())

    def test_honours_retry_after(self):
        slept = []
        client, _ = make_client(
            [
                error_response(429, "RATE_LIMIT_EXCEEDED", {"retry-after": "60"}),
                json_response(200, {}),
            ],
            slept,
        )
        client.request("GET", "/v1/payment")
        self.assertTrue(slept and slept[0] >= 60, f"slept={slept}")

    def test_create_without_key_not_retried(self):
        client, transport = make_client([error_response(500, "INTERNAL_ERROR")])
        with self.assertRaises(ApiError):
            client.create_payment({"price_amount": "1"})
        self.assertEqual(len(transport.calls), 1)

    def test_create_with_key_retries_and_waits_out_consistency_window(self):
        slept = []
        client, transport = make_client(
            [error_response(500, "INTERNAL_ERROR"), json_response(201, {"payment_id": "pzi_x"})],
            slept,
        )
        client.create_payment({"price_amount": "1"}, "order-12345")
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(transport.calls[0]["headers"].get("Idempotency-Key"), "order-12345")
        self.assertTrue(
            slept and slept[0] >= CONSISTENCY_WINDOW_SECONDS, f"slept={slept}"
        )

    def test_transport_failure_on_create_not_retried(self):
        client, transport = make_client([TransportError("timeout")])
        with self.assertRaises(TransportError):
            client.create_payment({"price_amount": "1"})
        self.assertEqual(len(transport.calls), 1)

    def test_404_maps_to_payment_not_found(self):
        client, _ = make_client([error_response(404, "PAYMENT_NOT_FOUND")])
        with self.assertRaises(ApiError) as ctx:
            client.request("GET", "/v1/payment/nope")
        self.assertEqual(ctx.exception.error_code, "PAYMENT_NOT_FOUND")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_client_preserves_decimals_end_to_end(self):
        client, _ = make_client([Response(200, '{"pay_amount":0.123456789012345678}')])
        body = client.request("GET", "/v1/payment/x")
        self.assertEqual(body["pay_amount"], Decimal("0.123456789012345678"))
        self.assertEqual(format(body["pay_amount"], "f"), "0.123456789012345678")

    def test_outbound_amount_keeps_all_18_digits_as_json_number(self):
        transport = FakeTransport([json_response(201, {"payment_id": "pzi_x"})])
        payzum = Payzum(KEY, Payzum.BASE_URL, transport)
        payzum.payments.create("0.123456789012345678", "usd", "usdcmatic")
        sent = transport.calls[0]["body"]
        self.assertIn('"price_amount":0.123456789012345678', sent)
        self.assertNotIn('"price_amount":"', sent)

    def test_only_named_field_unquoted_not_lookalike_values(self):
        transport = FakeTransport([json_response(201, {})])
        payzum = Payzum(KEY, Payzum.BASE_URL, transport)
        payzum.payments.create("49.99", "usd", "usdcmatic", order_id="ORDER-49.99")
        self.assertIn('"order_id":"ORDER-49.99"', transport.calls[0]["body"])

    def test_decimal_amount_accepted(self):
        transport = FakeTransport([json_response(201, {})])
        payzum = Payzum(KEY, Payzum.BASE_URL, transport)
        payzum.payments.create(Decimal("0.123456789012345678"), "usd", "usdcmatic")
        self.assertIn('"price_amount":0.123456789012345678', transport.calls[0]["body"])

    def test_api_key_only_on_authenticated_endpoints(self):
        client, transport = make_client([json_response(200, {})])
        client.request("GET", "/v1/currencies", authenticated=False)
        self.assertNotIn("x-api-key", transport.calls[-1]["headers"])

        client, transport = make_client([json_response(200, {})])
        client.request("GET", "/v1/payment")
        self.assertEqual(transport.calls[-1]["headers"].get("x-api-key"), KEY)

    def test_config_guards(self):
        with self.assertRaises(PayzumError):
            Config("too-short")
        self.assertEqual(Config("k" * 32).api_key, "k" * 32)
        with self.assertRaises(PayzumError):
            Config(KEY, "http://merchant.payzum.com")


class TestResourceGuards(unittest.TestCase):
    def test_direct_plus_all_rejected_locally(self):
        payzum = Payzum(KEY, Payzum.BASE_URL, FakeTransport([]))
        with self.assertRaises(PayzumError):
            payzum.payments.create("49.99", "usd", "all", pricing_mode="direct")

    def test_bad_sort_by_rejected(self):
        payzum = Payzum(KEY, Payzum.BASE_URL, FakeTransport([]))
        with self.assertRaises(PayzumError):
            payzum.payments.list(sort_by="nonsense")

    def test_malformed_invoice_id_rejected_locally(self):
        payzum = Payzum(KEY, Payzum.BASE_URL, FakeTransport([]))
        with self.assertRaises(PayzumError):
            payzum.invoices.status("not-an-id")

    def test_nonpositive_amount_rejected_locally(self):
        payzum = Payzum(KEY, Payzum.BASE_URL, FakeTransport([]))
        with self.assertRaises(PayzumError):
            payzum.payments.create("0", "usd", "all")
        with self.assertRaises(PayzumError):
            payzum.payments.create("abc", "usd", "all")

    def test_health_hits_v1_status_unauthenticated(self):
        transport = FakeTransport([json_response(200, {"status": "ok"})])
        payzum = Payzum(KEY, Payzum.BASE_URL, transport)
        body = payzum.health()
        self.assertEqual(body.get("status"), "ok")
        self.assertTrue(transport.calls[0]["url"].endswith("/v1/status"))
        self.assertNotIn("x-api-key", transport.calls[0]["headers"])


if __name__ == "__main__":
    unittest.main()
