"""Official Python SDK for the Payzum crypto payment API.

Accept stablecoin and crypto payments, verify IPN webhooks. Zero runtime
dependencies; money never touches a float.
"""

from .client import (
    CONSISTENCY_WINDOW_SECONDS,
    PRODUCTION_URL,
    SANDBOX_URL,
    Client,
    Config,
)
from .errors import (
    ERROR_CODES,
    RETRYABLE_CODES,
    SIGNATURE_FAILURE_REASONS,
    ApiError,
    PayzumError,
    SignatureError,
    TransportError,
    error_code_from_string,
    is_retryable_code,
)
from .jsonx import decode, decode_lossless, encode_with_exact_numbers
from .payzum import Payzum
from .resources import Currencies, Invoices, Payments, Rates
from .status import PAYMENT_STATUSES, PaymentStatus
from .transport import Response, Transport, UrllibTransport
from .version import VERSION
from .webhooks import (
    IPN_EVENT_TYPES,
    Verifier,
    ipn_event_type_from_string,
)

__version__ = VERSION

#: Production API host. ``api.payzum.com`` does NOT serve the API.
BASE_URL = PRODUCTION_URL

__all__ = [
    "ApiError",
    "BASE_URL",
    "CONSISTENCY_WINDOW_SECONDS",
    "Client",
    "Config",
    "Currencies",
    "ERROR_CODES",
    "IPN_EVENT_TYPES",
    "Invoices",
    "PAYMENT_STATUSES",
    "PRODUCTION_URL",
    "PaymentStatus",
    "Payments",
    "Payzum",
    "PayzumError",
    "RETRYABLE_CODES",
    "Rates",
    "Response",
    "SANDBOX_URL",
    "SIGNATURE_FAILURE_REASONS",
    "SignatureError",
    "Transport",
    "TransportError",
    "UrllibTransport",
    "VERSION",
    "Verifier",
    "decode",
    "decode_lossless",
    "encode_with_exact_numbers",
    "error_code_from_string",
    "ipn_event_type_from_string",
    "is_retryable_code",
    "is_ready",
]


def is_ready() -> bool:
    """Kept from the 0.0.1 name-reservation release so nothing that probed it
    breaks; the client above is the real thing.

    .. deprecated:: 0.1.0 Construct :class:`Payzum` instead.
    """
    return True
