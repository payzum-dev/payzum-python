"""HTTP client for the Payzum API: auth, retries, backoff, typed errors.

Retry policy, and why it is narrower than it looks like it could be:

Only three of the sixteen error codes are retryable. QUOTA_EXCEEDED arrives as
a 429 and is NOT one of them — it means too many invoices are open, so
retrying makes the situation worse. Treating every 429 as retryable is the
obvious mistake.

More importantly, an unsafe request is never retried automatically. Invoice
creation accepts an Idempotency-Key, but the API documents that as best-effort
with roughly 60 seconds of eventual consistency, and it does not enforce
order_id uniqueness. So a blind retry can create a second real invoice, and a
second charge. Without a key the SDK refuses to retry at all; with one it
still waits out the consistency window first.
"""

from __future__ import annotations

import math
import random
import time
from typing import Callable, Iterable, Mapping, Optional, Union

from . import jsonx
from .errors import ApiError, PayzumError, TransportError
from .transport import Response, Transport, UrllibTransport
from .version import VERSION

PRODUCTION_URL = "https://merchant.payzum.com"
#: Isolated data and separate API keys from production.
SANDBOX_URL = "https://staging.payzum.com"

#: Seconds to wait before the first safe retry of a create call.
CONSISTENCY_WINDOW_SECONDS = 60

Sleeper = Callable[[int], None]


class Config:
    """Client configuration, validated once at construction."""

    def __init__(
        self,
        api_key: str,
        base_url: str = PRODUCTION_URL,
        timeout_seconds: int = 30,
        max_retries: int = 3,
    ) -> None:
        # The server checks length >= 32 and nothing else — no hex, no fixed
        # 64. Being stricter here would reject keys the API accepts, which is
        # a worse failure than a round trip: it is unexplainable from the
        # caller's side.
        if len(api_key) < 32:
            raise PayzumError(
                "Payzum API key looks wrong: it must be at least 32 characters. "
                "Keys come from Dashboard → Settings → API Keys."
            )
        if not base_url.startswith("https://"):
            raise PayzumError("base_url must be https — API keys travel in a header.")
        if timeout_seconds < 1:
            raise PayzumError("timeout_seconds must be at least 1.")

        self.api_key = api_key
        self.base_url = base_url
        #: Per-request timeout. Every external call needs one.
        self.timeout_seconds = timeout_seconds
        #: Attempts after the first, for retryable failures only.
        self.max_retries = max_retries

    @classmethod
    def sandbox(cls, api_key: str) -> "Config":
        return cls(api_key, SANDBOX_URL)


class Client:
    def __init__(
        self,
        config: Config,
        transport: Optional[Transport] = None,
        sleeper: Optional[Sleeper] = None,
    ) -> None:
        self._config = config
        self._transport = transport if transport is not None else UrllibTransport()
        #: Injectable so tests exercise backoff without waiting for it.
        self._sleeper: Sleeper = sleeper if sleeper is not None else lambda s: time.sleep(s)

    def request(
        self,
        method: str,
        path: str,
        query: Optional[Mapping[str, Union[str, int]]] = None,
        json_body: Optional[Mapping[str, object]] = None,
        extra_headers: Optional[Mapping[str, str]] = None,
        authenticated: bool = True,
        retryable: bool = True,
        exact_numeric_fields: Iterable[str] = (),
        # Floor for every backoff of this request. Used by invoice creation to
        # wait out the idempotency consistency window before the first retry.
        min_retry_delay_seconds: int = 0,
    ) -> dict:
        """Perform a request and return the decoded body."""
        headers = self._build_headers(extra_headers or {}, authenticated, json_body is not None)

        body: Optional[str] = None
        if json_body is not None:
            # Amounts stay strings/Decimals all the way here and become numbers
            # only in the encoded text, so no float ever touches them.
            body = jsonx.encode_with_exact_numbers(json_body, list(exact_numeric_fields))

        url = self._config.base_url.rstrip("/") + path
        attempt = 0

        while True:
            try:
                response = self._transport.send(
                    method, url, headers, query or {}, body, self._config.timeout_seconds
                )
            except TransportError:
                # No response came back, so the server's state is unknown.
                if not retryable or attempt >= self._config.max_retries:
                    raise
                attempt += 1
                self._backoff(attempt, None, min_retry_delay_seconds)
                continue

            if 200 <= response.status < 300:
                return {} if response.body == "" else jsonx.decode_lossless(response.body)

            error = self._to_api_error(response)

            if not retryable or not error.is_retryable() or attempt >= self._config.max_retries:
                raise error

            attempt += 1
            self._backoff(attempt, error.retry_after_seconds, min_retry_delay_seconds)

    def create_payment(self, payload: Mapping[str, object], idempotency_key: Optional[str] = None) -> dict:
        """Create an invoice.

        Retries are enabled only when the caller supplies an Idempotency-Key,
        and even then the first backoff clears the consistency window rather
        than firing immediately. See the module docblock for why.
        """
        headers: dict[str, str] = {}
        if idempotency_key is not None:
            if idempotency_key == "" or len(idempotency_key) > 255:
                raise PayzumError("Idempotency-Key must be 1-255 characters.")
            headers["Idempotency-Key"] = idempotency_key

        return self.request(
            "POST",
            "/v1/payment",
            json_body=payload,
            extra_headers=headers,
            retryable=idempotency_key is not None,
            exact_numeric_fields=("price_amount",),
            min_retry_delay_seconds=CONSISTENCY_WINDOW_SECONDS,
        )

    # ------------------------------------------------------------ internals

    def _build_headers(
        self, extra: Mapping[str, str], authenticated: bool, has_body: bool
    ) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": f"payzum-python/{VERSION}",
        }
        headers.update(extra)

        if authenticated:
            headers["x-api-key"] = self._config.api_key
        if has_body:
            headers["Content-Type"] = "application/json"

        return headers

    def _to_api_error(self, response: Response) -> ApiError:
        try:
            body = jsonx.decode(response.body)
        except PayzumError:
            body = {"code": "UNKNOWN", "message": response.body.strip() or "Non-JSON error response"}

        return ApiError.from_response(response.status, body, response.retry_after_seconds())

    def _backoff(self, attempt: int, retry_after: Optional[int], floor_seconds: int = 0) -> None:
        """Honour Retry-After when the server sends it; otherwise exponential
        backoff with jitter, so a fleet of workers does not resynchronise into
        a thundering herd after a shared outage. Either way the delay never
        dips below the per-request floor (the consistency window, for creates).
        """
        base = max(retry_after if retry_after is not None else 2**attempt, floor_seconds)
        jitter = random.randint(0, 1000) / 1000
        self._sleeper(math.ceil(base + jitter))
