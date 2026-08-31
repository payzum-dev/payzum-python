"""JSON handling that does not silently round money.

The payments surface returns monetary fields as JSON numbers, frozen for
NowPayments compatibility. ``json.loads`` turns those into ``float``, so
``0.123456789012345678`` comes back as ``0.12345678901234568`` — digits gone,
with no error and no warning.

Python has the clean fix the other SDKs had to hand-roll: ``parse_float``
receives the *raw literal*, so every decimal number arrives as an exact
:class:`decimal.Decimal`. Integers stay ``int`` (arbitrary precision — nothing
to lose there).

Worth being honest about the ceiling: the gateway itself casts these values to
double before serialising, so the digits are already lost upstream. What this
buys is that the SDK adds no *further* loss and hands back exactly what the
server sent. For genuinely exact amounts, read the buyer surface
(``GET /v1/invoices/{id}/status``), whose amounts are decimal strings end to
end.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Iterable, Mapping

from .errors import PayzumError


def decode_lossless(raw: str) -> dict:
    """Decode JSON with every decimal number preserved as an exact Decimal."""
    try:
        decoded = json.loads(raw, parse_float=Decimal)
    except (json.JSONDecodeError, ValueError) as exc:
        raise PayzumError(f"Malformed JSON in API response: {exc}") from exc
    if not isinstance(decoded, dict):
        raise PayzumError("Malformed JSON in API response: expected an object")
    return decoded


def decode(raw: str) -> dict:
    """Plain decode, for payloads with no monetary fields."""
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise PayzumError(f"Malformed JSON in API response: {exc}") from exc
    if not isinstance(decoded, dict):
        raise PayzumError("Malformed JSON in API response: expected an object")
    return decoded


def encode_with_exact_numbers(data: Mapping[str, Any], numeric_fields: Iterable[str]) -> str:
    """Encode a payload, emitting the named fields as JSON numbers without ever
    turning them into a float.

    The API types ``price_amount`` as a JSON number, so it cannot be sent as a
    string — but casting ``"0.123456789012345678"`` to float to satisfy that
    produces ``0.12345678901234568`` on the wire. Which is the same silent
    rounding this SDK exists to avoid, only on the way out.

    So: encode with the amount as a string, then strip the quotes around
    exactly those fields (anchored on the encoded key, so a lookalike value
    elsewhere cannot match). The value reaches the API as a number with every
    digit the caller supplied.
    """
    prepared = dict(data)
    for field in numeric_fields:
        value = prepared.get(field)
        if isinstance(value, Decimal):
            prepared[field] = format(value, "f")

    encoded = json.dumps(prepared, separators=(",", ":"))

    for field in numeric_fields:
        value = prepared.get(field)
        if not isinstance(value, str):
            continue
        needle = json.dumps(field) + ':"' + value + '"'
        replacement = json.dumps(field) + ":" + value
        encoded = encoded.replace(needle, replacement)

    return encoded
