# payzum — official Python SDK

Accept crypto and stablecoin payments (USDC/USDT, multi-chain) with
[Payzum](https://merchant.payzum.com). Non-custodial — funds settle to your own
wallet. Zero runtime dependencies.

```
pip install payzum
```

You need two values from your [Payzum dashboard](https://merchant.payzum.com):
an **API key** and a **webhook secret**.

## Charge a customer

```python
from payzum import Payzum

payzum = Payzum("your-api-key")             # Payzum.sandbox(...) for staging

invoice = payzum.payments.create(
    price_amount="49.99",                   # a string or Decimal — never a float
    price_currency="usd",
    pay_currency="all",                     # the buyer picks the coin on the checkout page
    order_id="ORDER-12345",
    ipn_callback_url="https://your-shop.example/payzum/ipn",
    idempotency_key="ORDER-12345",          # lets the SDK retry safely on network hiccups
)
redirect(invoice["invoice_url"])            # send the buyer to the hosted checkout
```

That's the whole flow: create, redirect, and wait for the webhook. Which coins
the buyer can pick is configured in your dashboard, not in code.

## Get notified when it's paid

Payzum POSTs a signed webhook (IPN) to your `ipn_callback_url`. Hand the SDK
the **raw request body** and the headers — it finds the right header, checks
the signature and rejects replays, all by itself:

```python
from payzum import Payzum, PaymentStatus, SignatureError

try:
    data = Payzum.webhooks(webhook_secret).verify_payment_ipn(
        request.get_data(),                 # RAW bytes, before any parsing
        request.headers,
    )
except SignatureError:
    return "bad signature", 401

status = PaymentStatus.from_merchant(data["payment_status"])
if status.is_paid():
    fulfil_order(data["order_id"])          # only fulfil on is_paid()
```

Deliveries can arrive more than once — deduplicate on
`verifier.event_id(request.headers)` if a repeat must be a no-op on your side.

## Statuses

A payment is always in one of five states: `waiting`, `partially_paid`,
`finished`, `expired`, `failed`. Everything you need is on the enum:

- `status.is_paid()` — safe to fulfil (covers overpayment too).
- `status.is_terminal()` — nothing further will happen.
- `PaymentStatus.from_merchant(...)` raises on anything unexpected, so a
  surprise value can never be mistaken for "paid".

## Errors

Everything raises a typed exception: `ApiError` (with `.error_code`, e.g.
`AMOUNT_BELOW_MINIMUM`, to branch on), `SignatureError` for webhooks,
`TransportError` for network failures, `PayzumError` for local validation.

Transient failures are retried for you, honouring the server's back-off hints.
A create is **only** retried when you pass an `idempotency_key`, and then in a
way that cannot double-charge.

## Amounts are exact

Amounts go in as `str`/`Decimal` and come back as `Decimal` — the SDK never
converts money through a `float` in either direction.

## Testing your integration

`Payzum(api_key, transport=...)` accepts any object with the
`Transport.send()` shape, so you can test without touching the network. The
SDK's own suite runs with `python -m unittest discover -s tests`.

Docs: <https://merchant.payzum.com/docs> · llms: <https://merchant.payzum.com/llms.txt>
