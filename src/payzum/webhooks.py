"""Verification for the three webhook families Payzum sends.

They are NOT interchangeable, and confusing them is the most expensive mistake
you can make with this API — 20 of the 21 Payzum cart plugins read
``x-payzum-signature`` (the mass-payout header) for a payment IPN. The
signature never verifies, deliveries 401, the gateway retries five times and
dead-letters, and the order is silently never fulfilled.

==================  =============  ====================  ====================
payment IPN         HMAC-SHA-512   x-nowpayments-sig     key-sorted JSON
CoinPayments IPN    HMAC-SHA-512   HMAC                  form-urlencoded
mass payout         HMAC-SHA-256   X-Payzum-Signature    JSON, not sorted
==================  =============  ====================  ====================

Header names are fixed and owned by this class. They are deliberately not
constructor arguments: a setting is an invitation to fill it with the wrong
value, which is precisely how the plugins went wrong.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from decimal import Decimal
from typing import Mapping, Optional, Sequence, Union

from . import jsonx
from .errors import SignatureError

RawBody = Union[str, bytes]
WebhookHeaders = Mapping[str, Union[str, Sequence[str], None]]

#: The five event types the payment IPN emits — five, not two. An integration
#: that handles only paid/expired silently discards the three others,
#: including the two that matter for security.
#:
#: There is deliberately no ``invoice.partial`` and no cancellation event: the
#: gateway sends no IPN for those — they are only observable by polling.
IPN_EVENT_TYPES: tuple[str, ...] = (
    "invoice.paid",
    "invoice.expired",
    "late_deposit_received",
    "wrong_token_received",
    "suspicious_token_received",
)

_HEADER_PAYMENT_IPN = "x-nowpayments-sig"
#: Mass-payout webhooks. Note this is NOT the payment IPN header.
_HEADER_MASS_PAYOUT = "x-payzum-signature"
#: CoinPayments-mode merchants.
_HEADER_COINPAYMENTS = "hmac"
_HEADER_EVENT_ID = "x-payzum-event-id"


def ipn_event_type_from_string(value: str) -> Optional[str]:
    """Narrow a wire value to a known IPN event type, or ``None`` for future ones."""
    return value if value in IPN_EVENT_TYPES else None


def _body_bytes(raw_body: RawBody) -> bytes:
    return raw_body.encode("utf-8") if isinstance(raw_body, str) else bytes(raw_body)


def _body_str(raw_body: RawBody) -> str:
    return raw_body if isinstance(raw_body, str) else bytes(raw_body).decode("utf-8")


class Verifier:
    def __init__(self, secret: str, replay_window_seconds: int = 600) -> None:
        if secret == "":
            raise SignatureError(
                SignatureError.REASON_MALFORMED,
                "Webhook secret is empty. It is shown once, at merchant creation or rotation.",
            )
        self._secret = secret
        #: Reject events older (or further in the future) than this.
        self._replay_window_seconds = replay_window_seconds

    def verify_payment_ipn(
        self,
        raw_body: RawBody,
        headers: WebhookHeaders,
        now_epoch_seconds: Optional[int] = None,
    ) -> dict:
        """Verify a payment IPN and return its decoded payload.

        ``raw_body`` must be the bytes exactly as received, before any parsing.
        ``now_epoch_seconds`` is injectable so tests never touch the clock.
        """
        self._assert_signature(raw_body, headers, _HEADER_PAYMENT_IPN, "sha512")

        payload = jsonx.decode_lossless(_body_str(raw_body))
        self._assert_fresh(payload.get("event_at"), now_epoch_seconds)

        return payload

    def verify_mass_payout(
        self,
        raw_body: RawBody,
        headers: WebhookHeaders,
        now_epoch_seconds: Optional[int] = None,
    ) -> dict:
        """Verify a mass-payout webhook and return its decoded payload."""
        self._assert_signature(raw_body, headers, _HEADER_MASS_PAYOUT, "sha256")

        payload = jsonx.decode_lossless(_body_str(raw_body))
        self._assert_fresh(payload.get("eventAt"), now_epoch_seconds)

        return payload

    def verify_coinpayments_ipn(self, raw_body: RawBody, headers: WebhookHeaders) -> dict:
        """Verify a CoinPayments-shaped IPN and return its decoded form fields.

        There is no freshness check here because the CoinPayments payload
        carries no timestamp — a replay window is simply not possible.
        Deduplicating on ``ipn_id`` is the only defence available, and it is
        the caller's job.
        """
        self._assert_signature(raw_body, headers, _HEADER_COINPAYMENTS, "sha512")

        from urllib.parse import parse_qsl

        return dict(parse_qsl(_body_str(raw_body), keep_blank_values=True))

    def event_id(self, headers: WebhookHeaders) -> Optional[str]:
        """The id to deduplicate on, if the delivery carries one.

        Retries reuse it, so a second delivery with the same id must be a
        no-op rather than a second fulfilment. CoinPayments deliveries have no
        such header — use the ``ipn_id`` field from the body instead.
        """
        return self._header(headers, _HEADER_EVENT_ID)

    # ------------------------------------------------------------ internals

    def _assert_signature(
        self, raw_body: RawBody, headers: WebhookHeaders, header_name: str, algo: str
    ) -> None:
        provided = self._header(headers, header_name)

        if provided is None or provided == "":
            raise SignatureError(
                SignatureError.REASON_MISSING_HEADER,
                f'Missing "{header_name}" header. If you are seeing "x-payzum-signature" '
                "instead, that is the mass-payout header and this is a different webhook family.",
            )

        digest = hashlib.sha512 if algo == "sha512" else hashlib.sha256
        expected = hmac.new(self._secret.encode("utf-8"), _body_bytes(raw_body), digest).hexdigest()

        # Hex is case-insensitive, so normalise before comparing.
        given = provided.strip().lower()
        if len(given) != len(expected) or not hmac.compare_digest(expected, given):
            raise SignatureError(
                SignatureError.REASON_BAD_SIGNATURE,
                "Webhook signature does not match. Verify against the RAW request bytes, "
                "before parsing the JSON — re-serialising reorders keys and breaks it.",
            )

    def _assert_fresh(self, event_at: object, now: Optional[int]) -> None:
        if isinstance(event_at, bool) or not isinstance(event_at, (int, str, Decimal)):
            raise SignatureError(
                SignatureError.REASON_MALFORMED,
                "Signed payload carries no usable timestamp, so replay cannot be ruled out.",
            )

        try:
            ts = int(event_at)
        except (ValueError, ArithmeticError):
            raise SignatureError(
                SignatureError.REASON_MALFORMED,
                "Signed payload carries no usable timestamp, so replay cannot be ruled out.",
            ) from None

        current = int(time.time()) if now is None else now

        # Guard both directions: a far-future timestamp is clock skew or a
        # forged replay, and neither should be accepted.
        if abs(current - ts) > self._replay_window_seconds:
            raise SignatureError(
                SignatureError.REASON_STALE_EVENT,
                f"Event timestamp {ts} is outside the {self._replay_window_seconds}s window "
                f"around {current}. The signature is valid — this is a replay guard.",
            )

    @staticmethod
    def _header(headers: WebhookHeaders, name: str) -> Optional[str]:
        """Case-insensitive header lookup, per RFC 9110.

        Frameworks normalise header casing differently — WSGI upper-cases into
        ``HTTP_X_NOWPAYMENTS_SIG``, some proxies lower-case. Matching
        case-sensitively would work in development and fail in production.
        """
        want = name.lower()

        for key, value in headers.items():
            normalised = str(key).replace("_", "-").lower()
            if normalised == want or normalised == "http-" + want:
                if value is None:
                    return None
                if isinstance(value, str):
                    return value
                return value[0] if len(value) > 0 else None

        return None
