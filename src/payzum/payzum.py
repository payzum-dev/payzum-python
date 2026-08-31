"""Entry point for the Payzum Python SDK.

::

    from payzum import Payzum

    payzum = Payzum("your-api-key")

    invoice = payzum.payments.create(
        price_amount="49.99",
        price_currency="usd",
        pay_currency="all",       # let the buyer choose
        order_id="ORDER-12345",
    )
    redirect(invoice["invoice_url"])

And in your webhook handler, against the RAW body::

    payload = payzum.webhooks(secret).verify_payment_ipn(
        request.get_data(),      # raw bytes, before any parsing
        request.headers,
    )
"""

from __future__ import annotations

from typing import Optional

from .client import PRODUCTION_URL, SANDBOX_URL, Client, Config, Sleeper
from .resources import Currencies, Invoices, Payments, Rates
from .transport import Transport
from .webhooks import Verifier


class Payzum:
    #: Production API host. ``api.payzum.com`` does NOT serve the API.
    BASE_URL = PRODUCTION_URL

    #: Sandbox host. Isolated data, separate API keys.
    SANDBOX_URL = SANDBOX_URL

    def __init__(
        self,
        api_key: str,
        base_url: str = PRODUCTION_URL,
        transport: Optional[Transport] = None,
        sleeper: Optional[Sleeper] = None,
    ) -> None:
        config = Config(api_key, base_url)
        self.client = Client(config, transport, sleeper)
        self.payments = Payments(self.client)
        self.invoices = Invoices(self.client)
        self.currencies = Currencies(self.client)
        self.rates = Rates(self.client)

    @classmethod
    def sandbox(
        cls,
        api_key: str,
        transport: Optional[Transport] = None,
        sleeper: Optional[Sleeper] = None,
    ) -> "Payzum":
        """Point the SDK at staging."""
        return cls(api_key, SANDBOX_URL, transport, sleeper)

    @staticmethod
    def webhooks(webhook_secret: str) -> Verifier:
        """Webhook verifier for the given signing secret.

        The secret is shown once, at merchant creation or rotation, and is
        separate from the API key.
        """
        return Verifier(webhook_secret)

    def health(self) -> dict:
        """API diagnostics (GET /v1/status). Public — useful as a connectivity
        probe before blaming your own network. (``/health`` exists separately
        for the platform's own probes and is deliberately not exposed here.)
        """
        return self.client.request("GET", "/v1/status", authenticated=False)
