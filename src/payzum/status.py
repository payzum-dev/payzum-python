"""One canonical status, mapped from the two vocabularies the API uses.

The merchant surface emits five values and the buyer surface emits six, with
no name in common. Without a single enum every integrator invents their own
mapping, and the interesting cases are exactly where they get it wrong.

Deliberately absent: overpaid. The gateway treats overpayment as internal —
excess and fees go to treasury and the merchant is told "paid". Overpaid cases
are resolved manually by support when a merchant raises one. The merchant
surface therefore has no way to detect it, and this SDK does not invent one.
The buyer surface does expose it; that asymmetry is intentional.
"""

from __future__ import annotations

import enum

from .errors import PayzumError


class PaymentStatus(str, enum.Enum):
    WAITING = "waiting"
    PARTIALLY_PAID = "partially_paid"
    FINISHED = "finished"
    EXPIRED = "expired"
    FAILED = "failed"

    @classmethod
    def from_merchant(cls, value: str) -> "PaymentStatus":
        """Map ``payment_status`` from the merchant surface.

        Only these five ever appear. ``unconfirmed`` does not exist — older
        docs listed it. ``overpaid`` arrives as ``finished`` and ``cancelled``
        as ``failed``.
        """
        try:
            return cls(value)
        except ValueError:
            expected = ", ".join(s.value for s in cls)
            raise PayzumError(
                f'Unknown merchant payment_status "{value}". Expected one of: {expected}'
            ) from None

    @classmethod
    def from_buyer(cls, value: str) -> "PaymentStatus":
        """Map ``status`` from the buyer surface (GET /v1/invoices/{id}/status).

        ``overpaid`` collapses to FINISHED and ``cancelled`` to FAILED, so that
        both surfaces produce the same canonical value for the same invoice.
        """
        mapping = {
            "pending": cls.WAITING,
            "partial": cls.PARTIALLY_PAID,
            "paid": cls.FINISHED,
            "overpaid": cls.FINISHED,
            "expired": cls.EXPIRED,
            "cancelled": cls.FAILED,
        }
        try:
            return mapping[value]
        except KeyError:
            raise PayzumError(f'Unknown buyer status "{value}"') from None

    def is_terminal(self) -> bool:
        """No further transitions happen from here."""
        return self in (PaymentStatus.FINISHED, PaymentStatus.EXPIRED, PaymentStatus.FAILED)

    def is_paid(self) -> bool:
        """The invoice was paid in full. Safe to fulfil."""
        return self is PaymentStatus.FINISHED


#: The five statuses, as wire strings.
PAYMENT_STATUSES: tuple[str, ...] = tuple(s.value for s in PaymentStatus)
