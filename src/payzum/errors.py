"""Typed errors, mirroring payzum-php and payzum-node.

The API's own guidance is to branch on ``code``, never on ``message`` —
message text changes between releases.
"""

from __future__ import annotations

from typing import Optional

#: The 16 error codes the API emits.
ERROR_CODES: tuple[str, ...] = (
    "API_KEY_MISSING",
    "API_KEY_MALFORMED",
    "API_KEY_NOT_FOUND",
    "MERCHANT_SUSPENDED",
    "RATE_LIMIT_EXCEEDED",
    "INVALID_REQUEST",
    "CURRENCY_NOT_SUPPORTED",
    "RATE_PROVIDER_DOWN",
    "QUOTA_EXCEEDED",
    "PAYMENT_NOT_FOUND",
    "INTERNAL_ERROR",
    "AMOUNT_BELOW_MINIMUM",
    "NO_ELIGIBLE_CURRENCIES",
    "RANGE_TOO_LARGE",
    "EXPORT_HISTORY_UNAVAILABLE",
    "REPORT_NOT_FOUND",
)

#: Only three codes are worth retrying.
#:
#: QUOTA_EXCEEDED is deliberately excluded even though it arrives as a 429: it
#: means too many invoices are open at once, so retrying makes it worse, not
#: better. Treating every 429 as retryable is the obvious mistake here.
RETRYABLE_CODES: frozenset[str] = frozenset(
    {"RATE_LIMIT_EXCEEDED", "INTERNAL_ERROR", "RATE_PROVIDER_DOWN"}
)


def error_code_from_string(code: Optional[str]) -> Optional[str]:
    """Narrow a wire value to a known code, or ``None`` for future ones.

    Unknown codes are surfaced (via :attr:`ApiError.raw_code`) rather than
    swallowed — the API may add some.
    """
    return code if code in ERROR_CODES else None


def is_retryable_code(code: Optional[str]) -> bool:
    return code in RETRYABLE_CODES


class PayzumError(Exception):
    """Base class for every error this SDK raises."""


class TransportError(PayzumError):
    """The request never produced a response — the server's state is unknown."""


class ApiError(PayzumError):
    """A structured error returned by the API.

    The canonical envelope is ``{statusCode, code, message}``. The buyer
    surface adds a legacy ``error`` slug (``invalid_invoice_id`` |
    ``rate_limited`` | ``not_found``) additively — it is exposed here but
    branching on ``code`` is preferred.
    """

    def __init__(
        self,
        status_code: int,
        raw_code: str,
        message: str,
        legacy_slug: Optional[str] = None,
        retry_after_seconds: Optional[int] = None,
    ) -> None:
        super().__init__(f"[{status_code} {raw_code}] {message}")
        self.status_code = status_code
        self.raw_code = raw_code
        #: The known code, or ``None`` when the API sent something new.
        self.error_code = error_code_from_string(raw_code)
        self.legacy_slug = legacy_slug
        self.retry_after_seconds = retry_after_seconds

    def is_retryable(self) -> bool:
        return is_retryable_code(self.error_code)

    @classmethod
    def from_response(
        cls, status_code: int, body: dict, retry_after: Optional[int]
    ) -> "ApiError":
        raw = body.get("code")
        slug = body.get("error")
        message = body.get("message")
        return cls(
            status_code=status_code,
            raw_code=raw if isinstance(raw, str) else "UNKNOWN",
            message=message if isinstance(message, str) else "Unknown API error",
            legacy_slug=slug if isinstance(slug, str) else None,
            retry_after_seconds=retry_after,
        )


class SignatureError(PayzumError):
    """A webhook failed verification and must not be acted on.

    Verification raises rather than returning a bool on purpose. A bool can be
    ignored by accident — ``verify(body, headers)`` on its own line runs and
    fulfils the order. An exception cannot be ignored by accident.
    """

    REASON_MISSING_HEADER = "missing_header"
    REASON_BAD_SIGNATURE = "bad_signature"
    REASON_STALE_EVENT = "stale_event"
    REASON_MALFORMED = "malformed_payload"

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


#: The reasons :class:`SignatureError` can carry.
SIGNATURE_FAILURE_REASONS: tuple[str, ...] = (
    SignatureError.REASON_MISSING_HEADER,
    SignatureError.REASON_BAD_SIGNATURE,
    SignatureError.REASON_STALE_EVENT,
    SignatureError.REASON_MALFORMED,
)
