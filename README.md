# payzum (Python)

Official SDK for [Payzum](https://payzum.com) — accept stablecoin and crypto
payments, and verify IPN webhooks.

```
pip install payzum
```

> ## ⚠️ This is a `0.0.1` placeholder
>
> It reserves the package name. **It contains no client yet.** A half-working
> payment client is worse than none, so this release deliberately ships only a
> version constant and the base URLs.
>
> Until v1 lands, integrate against the [REST API](https://merchant.payzum.com/docs)
> directly. If you do, read the webhook section below first — it is where
> integrations most often go wrong.

## What v1 will contain

- **Payments:** create, read (by payment id *or* your own `order_id`) and list
  invoices.
- **Buyer status:** the public, unauthenticated invoice status endpoint.
- **Rates:** `estimate` and `min-amount`, so you can reject an under-minimum
  amount before creating an invoice instead of getting `AMOUNT_BELOW_MINIMUM`.
- **Currencies:** the detailed catalogue, which tells you the chain of each
  asset — the flat list cannot, since `eth` appears once per chain.
- **Webhooks:** three separate verifiers, one per signature scheme, each with
  its header and algorithm fixed internally.
- **Typed errors:** all 16 API error codes, each classified as retryable or not.
- **Decimal-safe amounts:** no `float` ever crosses the public boundary.

Mass payouts (UTXO and EVM) are planned for v1.1.

## The webhook trap, if you are integrating by hand today

Payzum sends **three** kinds of signed webhook and none of them is
interchangeable:

| Webhook | Algorithm | Header |
|---|---|---|
| Payment IPN (default) | HMAC-SHA-512 | `x-nowpayments-sig` |
| Payment IPN, CoinPayments-mode merchants | HMAC-SHA-512 over a form-encoded body | `HMAC` |
| Mass payout | HMAC-SHA-256 | `X-Payzum-Signature` |

The payment IPN header is named after Payzum's NowPayments-compatible dialect,
which lets an existing NowPayments integration point at Payzum without code
changes. Using `X-Payzum-Signature` for a payment IPN is the single most common
bug with this API — the signature never verifies, deliveries get a 401, and
orders are silently never fulfilled.

Always verify against the **raw request bytes, before parsing the JSON**, and
compare in constant time. Header lookup is case-insensitive.

## Requirements

Python 3.9 or newer.

## Links

- Documentation: <https://merchant.payzum.com/docs>
- Machine-readable reference for AI agents: <https://merchant.payzum.com/llms.txt>
- Sandbox: <https://staging.payzum.com>

Note that `api.payzum.com` does **not** serve the API. Use
`merchant.payzum.com`.

## License

MIT — see [LICENSE](LICENSE).
