"""Process-local MI collateral reservation; no trading or order lifecycle authority.

The official CLOB balance is admitted only with a complete, empty account-wide
open-order read. Existing orders reserve balance at the venue, and this slice
does not estimate their remaining cash cost. Any such order fails closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from threading import RLock
from typing import Any
from uuid import uuid4

from polymaker.live_authorization import (
    LiveAuthorizationResult,
    LiveExecutionIdentity,
    live_authorization_request,
    validate_live_identity,
)

MI_REQUIRED_CAPITAL_MICRO = 25_000_000


class MICashReadinessError(RuntimeError):
    """Official collateral or account-wide order evidence cannot support a reserve."""


class CashReservationError(RuntimeError):
    """A process-local reservation cannot be created or released safely."""


@dataclass(frozen=True, slots=True)
class ConfirmedSpendableCollateral:
    identity: LiveExecutionIdentity
    amount_micro: int


class ReservationState(str, Enum):
    ACTIVE = "ACTIVE"
    RELEASED = "RELEASED"


@dataclass(frozen=True, slots=True)
class MICashReservation:
    reservation_id: str
    identity: LiveExecutionIdentity
    condition_id: str
    amount_micro: int
    state: ReservationState


class _ProcessReservationAuthority:
    """The one lock and record namespace for MI reservations in this process."""

    def __init__(self) -> None:
        self.lock = RLock()
        self.records: dict[str, MICashReservation] = {}
        self.active: dict[tuple[LiveExecutionIdentity, str], str] = {}


_PROCESS_AUTHORITY = _ProcessReservationAuthority()


def _parse_collateral_micro(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise MICashReadinessError("COLLATERAL_INVALID")
    if isinstance(value, str) and (not value or not value.isascii() or not value.isdecimal()):
        raise MICashReadinessError("COLLATERAL_INVALID")
    amount = int(value)
    if amount < 0:
        raise MICashReadinessError("COLLATERAL_INVALID")
    return amount


async def read_confirmed_spendable_collateral(
    config: Any, gateway: Any, authorization: LiveAuthorizationResult,
) -> ConfirmedSpendableCollateral:
    """Read official CLOB evidence without granting or invoking write capability.

    The installed official SDK's unfiltered get_open_orders() reads every page.
    Gateway.open_orders() delegates to it and fails on malformed responses.
    A nonempty account-wide result is denied because this slice does not
    calculate the remaining collateral committed to those orders.
    """
    if not isinstance(authorization, LiveAuthorizationResult) or (
        authorization.authorized is not True or authorization.identity is None
    ):
        raise MICashReadinessError("AUTHORIZATION_REQUIRED")

    def check_identity() -> None:
        request = live_authorization_request(
            config, gateway, explicit_write_mode=False, armed=False,
        )
        if request.live_environment is not True:
            raise MICashReadinessError("LIVE_IDENTITY_REQUIRED")
        current = validate_live_identity(request)
        if current.authorized is not True or current.identity != authorization.identity:
            raise MICashReadinessError("IDENTITY_MISMATCH")

    check_identity()
    try:
        raw = await gateway.balance_allowance()
        orders = await gateway.open_orders()
    except Exception as exc:
        raise MICashReadinessError("OFFICIAL_CASH_READ_FAILED") from exc
    check_identity()
    if not isinstance(orders, list):
        raise MICashReadinessError("ACCOUNT_ORDERS_INVALID")
    if orders:
        raise MICashReadinessError("ACCOUNT_OPEN_ORDERS_UNRESOLVED")
    if not isinstance(raw, dict) or "balance" not in raw:
        raise MICashReadinessError("COLLATERAL_UNAVAILABLE")
    return ConfirmedSpendableCollateral(
        authorization.identity, _parse_collateral_micro(raw["balance"]),
    )


class MICashReservationBook:
    """Atomic reservations per collateral funder in one process; no restart guarantee."""

    __slots__ = ()

    def effective_available_micro(self, evidence: ConfirmedSpendableCollateral) -> int:
        if not isinstance(evidence, ConfirmedSpendableCollateral) or (
            not isinstance(evidence.identity, LiveExecutionIdentity)
            or type(evidence.amount_micro) is not int or evidence.amount_micro < 0
        ):
            raise CashReservationError("COLLATERAL_EVIDENCE_INVALID")
        authority = _PROCESS_AUTHORITY
        with authority.lock:
            reserved = sum(
                record.amount_micro for record in authority.records.values()
                if record.identity.funder == evidence.identity.funder
                and record.state is ReservationState.ACTIVE
            )
            return evidence.amount_micro - reserved

    def reserve(
        self, evidence: ConfirmedSpendableCollateral, condition_id: str,
        *, current_identity: LiveExecutionIdentity,
    ) -> MICashReservation:
        if not isinstance(evidence, ConfirmedSpendableCollateral) or (
            not isinstance(evidence.identity, LiveExecutionIdentity)
            or type(evidence.amount_micro) is not int or evidence.amount_micro < 0
        ):
            raise CashReservationError("COLLATERAL_EVIDENCE_INVALID")
        if not isinstance(current_identity, LiveExecutionIdentity) or (
            current_identity != evidence.identity
        ):
            raise CashReservationError("IDENTITY_MISMATCH")
        if not isinstance(condition_id, str) or not condition_id.strip():
            raise CashReservationError("MARKET_IDENTITY_MISSING")
        condition_id = condition_id.strip().lower()

        key = (evidence.identity, condition_id)
        authority = _PROCESS_AUTHORITY
        with authority.lock:
            reserved = sum(
                record.amount_micro for record in authority.records.values()
                if record.identity.funder == evidence.identity.funder
                and record.state is ReservationState.ACTIVE
            )
            if evidence.amount_micro < reserved:
                raise CashReservationError("COLLATERAL_BELOW_ACTIVE_RESERVATIONS")
            active_id = authority.active.get(key)
            if active_id is not None:
                record = authority.records.get(active_id)
                if record is None or record.state is not ReservationState.ACTIVE or (
                    record.amount_micro != MI_REQUIRED_CAPITAL_MICRO
                ):
                    raise CashReservationError("RESERVATION_STATE_CONFLICT")
                return record
            if evidence.amount_micro - reserved < MI_REQUIRED_CAPITAL_MICRO:
                raise CashReservationError("INSUFFICIENT_SPENDABLE_COLLATERAL")
            record = MICashReservation(
                str(uuid4()), evidence.identity, condition_id,
                MI_REQUIRED_CAPITAL_MICRO, ReservationState.ACTIVE,
            )
            authority.records[record.reservation_id] = record
            authority.active[key] = record.reservation_id
            return record

    def release(
        self, reservation_id: str, *, current_identity: LiveExecutionIdentity,
        condition_id: str,
    ) -> MICashReservation:
        if not isinstance(condition_id, str) or not condition_id.strip():
            raise CashReservationError("MARKET_IDENTITY_MISSING")
        condition_id = condition_id.strip().lower()
        authority = _PROCESS_AUTHORITY
        with authority.lock:
            record = authority.records.get(reservation_id)
            if record is None or record.identity != current_identity or (
                record.condition_id != condition_id
            ):
                raise CashReservationError("RESERVATION_OWNERSHIP_MISMATCH")
            if record.state is ReservationState.RELEASED:
                return record
            key = (record.identity, record.condition_id)
            if authority.active.get(key) != reservation_id:
                raise CashReservationError("RESERVATION_STATE_CONFLICT")
            released = MICashReservation(
                record.reservation_id, record.identity, record.condition_id,
                record.amount_micro, ReservationState.RELEASED,
            )
            authority.records[reservation_id] = released
            del authority.active[key]
            return released
