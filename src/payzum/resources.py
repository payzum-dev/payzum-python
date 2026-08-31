"""API resources, mirroring payzum-php and payzum-node."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Optional, Union

from .client import Client
from .errors import PayzumError

Amount = Union[str, Decimal]

_AMOUNT_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
_INVOICE_ID_RE = re.compile(r"^pzi_[a-z0-9]{24,32}$")


def _amount_str(value: Amount, name: str) -> str:
    text = format(value, "f") if isinstance(value, Decimal) else str(value)
    if not _AMOUNT_RE.fullmatch(text) or Decimal(text) <= 0:
        raise PayzumError(f"{name} must be a positive numeric string.")
    return text


class Payments:
    """Invoices on the merchant surface."""

    def __init__(self, client: Client) -> None:
        self._client = client

    def create(
        self,
        price_amount: Amount,
        price_currency: str,
        # An NP code (`usdttrc20`), a bare symbol combined with `network`, or
        # `"all"` to let the buyer pick.
        pay_currency: str,
        *,
        order_id: Optional[str] = None,
        order_description: Optional[str] = None,
        network: Optional[str] = None,
        ipn_callback_url: Optional[str] = None,
        success_url: Optional[str] = None,
        cancel_url: Optional[str] = None,
        pricing_mode: str = "fiat",
        # Supply one to make a retry safe. See Client for why a retry without
        # it is never automatic.
        idempotency_key: Optional[str] = None,
    ) -> dict:
        """Create an invoice.

        Amounts are accepted as strings (or Decimal) so nothing is rounded on
        the way in.
        """
        # The API rejects this combination with INVALID_REQUEST; catching it
        # here saves a round trip and names the problem plainly.
        if pricing_mode == "direct" and pay_currency == "all":
            raise PayzumError(
                'pricing_mode "direct" cannot be combined with pay_currency "all": '
                "a direct price needs to know which asset it is denominated in."
            )

        payload: dict[str, object] = {
            # Stays a string here on purpose. The client encodes it as an exact
            # JSON number; casting to float would round it on the way out.
            "price_amount": _amount_str(price_amount, "price_amount"),
            "price_currency": price_currency,
            "pay_currency": pay_currency,
            "pricing_mode": pricing_mode,
        }
        for key, value in (
            ("order_id", order_id),
            ("order_description", order_description),
            ("network", network),
            ("ipn_callback_url", ipn_callback_url),
            ("success_url", success_url),
            ("cancel_url", cancel_url),
        ):
            if value is not None:
                payload[key] = value

        return self._client.create_payment(payload, idempotency_key)

    def get(self, id_or_order_id: str) -> dict:
        """Read one invoice.

        Accepts either the Payzum ``payment_id`` or your own ``order_id``, so
        there is no need to keep a mapping table on your side.
        """
        from urllib.parse import quote

        return self._client.request("GET", "/v1/payment/" + quote(id_or_order_id, safe=""))

    def list(
        self,
        limit: int = 10,
        page: int = 0,
        sort_by: str = "created_at",
        order_by: str = "desc",
    ) -> dict:
        """List invoices, newest first by default.

        Pagination is ``page`` (zero-based) plus ``limit`` — not an offset.
        Drafts created with pay_currency="all" that the buyer never resolved
        are not listed; fetch those by id.
        """
        if limit < 1 or limit > 100:
            raise PayzumError("limit must be between 1 and 100.")
        if page < 0:
            raise PayzumError("page is zero-based and cannot be negative.")
        # The server silently falls back to created_at for anything it does
        # not recognise, which turns a typo into quietly wrong ordering.
        if sort_by not in ("created_at", "updated_at"):
            raise PayzumError('sort_by must be "created_at" or "updated_at".')
        if order_by not in ("asc", "desc"):
            raise PayzumError('order_by must be "asc" or "desc".')

        return self._client.request(
            "GET",
            "/v1/payment",
            query={"limit": limit, "page": page, "sortBy": sort_by, "orderBy": order_by},
        )


class Invoices:
    """The buyer-facing invoice status endpoint.

    Unauthenticated, with its own rate limit keyed by client IP, so polling it
    does not eat the merchant's 60/minute budget.

    Two things worth knowing. The invoice id is a bearer token — roughly 120
    bits of entropy, and whoever holds it can read that invoice's status. Keep
    it out of access logs and shareable URLs. And unlike the merchant surface,
    the amounts here are exact decimal strings, so this is the surface to read
    when you need precision.
    """

    def __init__(self, client: Client) -> None:
        self._client = client

    def status(self, payment_id: str) -> dict:
        """Fetch buyer-facing status. No API key is sent.

        Returns camelCase names, decimal-string amounts, epoch-millisecond
        timestamps, and the buyer status vocabulary
        (pending|partial|paid|overpaid|expired|cancelled).
        """
        if not _INVOICE_ID_RE.fullmatch(payment_id):
            raise PayzumError(
                f'"{payment_id}" is not a Payzum invoice id '
                "(expected pzi_ + 24-32 lowercase alphanumerics)."
            )

        return self._client.request(
            "GET", f"/v1/invoices/{payment_id}/status", authenticated=False
        )


class Currencies:
    """Supported assets.

    Always read ``currencies_detailed``, never the flat ``currencies`` array:
    the flat one loses the chain for native assets, so ``eth`` appears four
    times — Arbitrum, Base, Ethereum and Optimism — with no way to tell them
    apart. The flat array is kept only for backwards compatibility.
    """

    def __init__(self, client: Client) -> None:
        self._client = client
        self._cache: Optional[list] = None

    def list(self, refresh: bool = False) -> list:
        """Every supported (asset, chain) pair, with contract address, decimals
        and per-network minimum.

        Cached for the lifetime of the client: the catalogue changes rarely
        and this endpoint is often called once per checkout render.
        """
        if self._cache is not None and not refresh:
            return self._cache

        body = self._client.request("GET", "/v1/currencies", authenticated=False)
        detailed = body.get("currencies_detailed", [])
        self._cache = list(detailed) if isinstance(detailed, list) else []
        return self._cache

    def find(self, code: str, refresh: bool = False) -> Optional[dict]:
        """Look up one asset by its API code, e.g. ``usdcmatic``."""
        for currency in self.list(refresh):
            if isinstance(currency, dict) and currency.get("code") == code:
                return currency
        return None

    def on_chain(self, chain: str, refresh: bool = False) -> list:
        """Assets available on one chain, e.g. ``polygon``."""
        return [
            c for c in self.list(refresh) if isinstance(c, dict) and c.get("chain") == chain
        ]


class Rates:
    """Conversion estimates and network minimums. Neither needs an API key."""

    def __init__(self, client: Client) -> None:
        self._client = client

    def estimate(self, amount: Amount, currency_from: str, currency_to: str) -> dict:
        """Estimate what a fiat amount comes to in a crypto asset.

        Note the response mixes types: ``estimated_amount`` is a decimal
        string while ``amount_from`` and ``min_amount_usd`` are numbers. That
        is deliberate on the API's side, and it is why monetary fields are
        marked per field rather than per endpoint.
        """
        return self._client.request(
            "GET",
            "/v1/estimate",
            query={
                "amount": _amount_str(amount, "amount"),
                "currency_from": currency_from,
                "currency_to": currency_to,
            },
            authenticated=False,
        )

    def min_amount(self, currency_from: str, currency_to: str) -> dict:
        """Minimum payable amount for a currency pair.

        Worth calling before creating an invoice: an amount below the network
        minimum is rejected with AMOUNT_BELOW_MINIMUM, and finding that out
        after the buyer has already committed is a bad moment to discover it.
        """
        return self._client.request(
            "GET",
            "/v1/min-amount",
            query={"currency_from": currency_from, "currency_to": currency_to},
            authenticated=False,
        )
