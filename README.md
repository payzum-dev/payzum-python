# payzum — official Python SDK

Accept crypto and stablecoin payments (USDC/USDT, multi-chain) with
[Payzum](https://merchant.payzum.com). Non-custodial — funds settle to your own
wallet.

- **Zero runtime dependencies.** Standard library only: `urllib` transport,
  `hmac` verification, `decimal` money.
- **Money never touches a float.** Responses decode with
  `json.loads(parse_float=Decimal)`, so `0.123456789012345678` arrives with
  every digit; outbound amounts are emitted as exact JSON numbers without ever
  being a `float`.
- **Webhook verification you cannot get wrong.** The `Verifier` owns the fixed
  signature headers for the three webhook families (they are *not*
  interchangeable), verifies over the RAW bytes in constant time, and enforces
  a 10-minute replay window on the signed timestamp.
- **A retry policy that will not double-charge.** Invoice creation is never
  retried without an `Idempotency-Key`; with one, the first retry still waits
  out the API's ~60 s idempotency consistency window.

```
pip install payzum
```

## Create an invoice

```python
from payzum import Payzum

payzum = Payzum("your-api-key")             # Payzum.sandbox(...) for staging

invoice = payzum.payments.create(
    price_amount="49.99",                   # a string (or Decimal) — never a float
    price_currency="usd",
    pay_currency="all",                     # the buyer picks the coin on the hosted checkout
    order_id="ORDER-12345",
    ipn_callback_url="https://your-shop.example/payzum/ipn",
    idempotency_key="ORDER-12345",          # makes a transport retry safe
)
redirect(invoice["invoice_url"])
```

`pay_currency="all"` defers the coin choice to the buyer, limited to the
allowlist configured in your Payzum dashboard (Merchants → Settings → Accepted
tokens) and enforced server-side.

## Verify an IPN

```python
from payzum import Payzum, SignatureError, PaymentStatus

try:
    data = Payzum.webhooks(webhook_secret).verify_payment_ipn(
        request.get_data(),                 # RAW bytes, before any parsing
        request.headers,
    )
except SignatureError:
    return "bad signature", 401

status = PaymentStatus.from_merchant(data["payment_status"])
if status.is_paid():                        # finished — covers overpayment too
    fulfil_order(data["order_id"])
```

The verifier reads the fixed `x-nowpayments-sig` header itself
(case-insensitively, WSGI `HTTP_` form included). Deduplicate deliveries on
`verifier.event_id(headers)` — retries reuse it.

## The status vocabulary

The merchant surface emits exactly five statuses: `waiting`,
`partially_paid`, `finished`, `expired`, `failed`. `PaymentStatus` models
those and raises on anything else — a sixth value would be a contract change
that should break loudly, not be guessed at. `from_buyer()` maps the buyer
surface's six values onto the same enum.

## Testing

The suite runs the shared webhook-signature corpus every Payzum SDK verifies:

```
python -m unittest discover -s tests
```

`Payzum(api_key, transport=...)` accepts any object with the
`Transport.send()` shape, so the client is testable without sockets.

Docs: <https://merchant.payzum.com/docs> · llms: <https://merchant.payzum.com/llms.txt>
