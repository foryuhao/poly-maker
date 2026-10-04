"""Offline MI cash evidence and process-local reservation contract."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import pytest
from eth_account import Account

from polymaker import mi_cash_reservation as cash_module
from polymaker.live_authorization import (
    LiveAuthorizationRequest,
    LiveExecutionIdentity,
    authorize_live_write,
)
from polymaker.mi_cash_reservation import (
    MI_REQUIRED_CAPITAL_MICRO,
    CashReservationError,
    ConfirmedSpendableCollateral,
    MICashReadinessError,
    MICashReservationBook,
    ReservationState,
    read_confirmed_spendable_collateral,
)

KEY_A = "0x" + "1" * 64
KEY_B = "0x" + "2" * 64
ADDRESS_A = Account.from_key(KEY_A).address
ADDRESS_B = Account.from_key(KEY_B).address
IDENTITY_A = LiveExecutionIdentity(ADDRESS_A, ADDRESS_A, 0)
IDENTITY_B = LiveExecutionIdentity(ADDRESS_B, ADDRESS_B, 0)
IDENTITY_SAME_FUNDER = LiveExecutionIdentity(ADDRESS_B, ADDRESS_A, 2)


@pytest.fixture(autouse=True)
def isolated_process_authority(monkeypatch):
    """Replace the private backend only for this test, never through a public API."""
    monkeypatch.setattr(cash_module, "_PROCESS_AUTHORITY", cash_module._ProcessReservationAuthority())


def evidence(amount_micro: int, identity: LiveExecutionIdentity = IDENTITY_A):
    return ConfirmedSpendableCollateral(identity, amount_micro)


@pytest.mark.parametrize(
    "cash,allowed,remaining",
    [
        (25_000_000, True, 0),
        (24_990_000, False, None),
        (40_000_000, True, 15_000_000),
        (24_999_999, False, None),
        (25_000_001, True, 1),
    ],
)
def test_exact_micro_collateral_boundary(cash, allowed, remaining):
    book = MICashReservationBook()
    if allowed:
        record = book.reserve(evidence(cash), "condition-A", current_identity=IDENTITY_A)
        assert record.amount_micro == MI_REQUIRED_CAPITAL_MICRO
        assert record.state is ReservationState.ACTIVE
        assert book.effective_available_micro(evidence(cash)) == remaining
    else:
        with pytest.raises(CashReservationError, match="INSUFFICIENT"):
            book.reserve(evidence(cash), "condition-A", current_identity=IDENTITY_A)
        assert book.effective_available_micro(evidence(cash)) == cash


def test_two_markets_cannot_reserve_the_same_collateral_and_release_restores_once():
    book = MICashReservationBook()
    cash = evidence(40_000_000)
    first = book.reserve(cash, "condition-A", current_identity=IDENTITY_A)
    with pytest.raises(CashReservationError, match="INSUFFICIENT"):
        book.reserve(cash, "condition-B", current_identity=IDENTITY_A)
    assert book.release(first.reservation_id, current_identity=IDENTITY_A,
                        condition_id="condition-A").state is ReservationState.RELEASED
    assert book.release(first.reservation_id, current_identity=IDENTITY_A,
                        condition_id="condition-A").state is ReservationState.RELEASED
    assert book.effective_available_micro(cash) == 40_000_000
    second = book.reserve(cash, "condition-B", current_identity=IDENTITY_A)
    assert second.reservation_id != first.reservation_id
    assert book.effective_available_micro(cash) == 15_000_000


def test_duplicate_reserve_is_idempotent_and_stale_release_cannot_release_new_reserve():
    book = MICashReservationBook()
    cash = evidence(25_000_000)
    first = book.reserve(cash, "condition-A", current_identity=IDENTITY_A)
    assert book.reserve(cash, "condition-A", current_identity=IDENTITY_A) == first
    assert book.effective_available_micro(cash) == 0
    book.release(first.reservation_id, current_identity=IDENTITY_A,
                 condition_id="condition-A")
    second = book.reserve(cash, "condition-A", current_identity=IDENTITY_A)
    assert second.reservation_id != first.reservation_id
    book.release(first.reservation_id, current_identity=IDENTITY_A,
                 condition_id="condition-A")
    assert book.effective_available_micro(cash) == 0


def test_condition_id_normalization_cannot_create_a_second_market_reservation():
    book = MICashReservationBook()
    cash = evidence(50_000_000)
    first = book.reserve(cash, "Condition-A", current_identity=IDENTITY_A)
    assert first.condition_id == "condition-a"
    assert book.reserve(cash, " condition-a ", current_identity=IDENTITY_A) == first
    assert book.effective_available_micro(cash) == 25_000_000


def test_duplicate_reserve_fails_closed_if_fresh_cash_falls_below_active_commitment():
    book = MICashReservationBook()
    book.reserve(evidence(25_000_000), "condition-A", current_identity=IDENTITY_A)
    with pytest.raises(CashReservationError, match="COLLATERAL_BELOW_ACTIVE_RESERVATIONS"):
        book.reserve(evidence(24_999_999), "condition-A", current_identity=IDENTITY_A)


def test_identity_and_market_ownership_cannot_be_inherited():
    book = MICashReservationBook()
    cash = evidence(25_000_000)
    first = book.reserve(cash, "condition-A", current_identity=IDENTITY_A)
    with pytest.raises(CashReservationError, match="IDENTITY_MISMATCH"):
        book.reserve(cash, "condition-A", current_identity=IDENTITY_B)
    with pytest.raises(CashReservationError, match="OWNERSHIP"):
        book.release(first.reservation_id, current_identity=IDENTITY_B,
                     condition_id="condition-A")
    with pytest.raises(CashReservationError, match="OWNERSHIP"):
        book.release(first.reservation_id, current_identity=IDENTITY_A,
                     condition_id="condition-B")
    # B has a separate identity ledger; it cannot reuse or release A's record.
    second = book.reserve(evidence(25_000_000, IDENTITY_B), "condition-A",
                          current_identity=IDENTITY_B)
    assert second.reservation_id != first.reservation_id


def test_different_signers_sharing_one_funder_cannot_double_reserve_collateral():
    book = MICashReservationBook()
    book.reserve(evidence(25_000_000), "condition-A", current_identity=IDENTITY_A)
    same_collateral_pool = evidence(25_000_000, IDENTITY_SAME_FUNDER)
    assert book.effective_available_micro(same_collateral_pool) == 0
    with pytest.raises(CashReservationError, match="INSUFFICIENT"):
        book.reserve(same_collateral_pool, "condition-B",
                     current_identity=IDENTITY_SAME_FUNDER)


def test_missing_cash_and_market_identity_deny_without_reservation():
    book = MICashReservationBook()
    for bad in (None, ConfirmedSpendableCollateral(IDENTITY_A, -1)):
        with pytest.raises(CashReservationError, match="COLLATERAL_EVIDENCE_INVALID"):
            book.reserve(bad, "condition-A", current_identity=IDENTITY_A)
    with pytest.raises(CashReservationError, match="MARKET_IDENTITY_MISSING"):
        book.reserve(evidence(25_000_000), "", current_identity=IDENTITY_A)


def test_concurrent_reservations_are_atomic():
    book = MICashReservationBook()
    cash = evidence(40_000_000)

    def attempt(market):
        try:
            return book.reserve(cash, market, current_identity=IDENTITY_A)
        except CashReservationError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, ("condition-A", "condition-B")))
    assert sum(record is not None for record in results) == 1
    assert book.effective_available_micro(cash) == 15_000_000


def test_two_books_cannot_allocate_the_same_25_to_two_markets():
    first_book, second_book = MICashReservationBook(), MICashReservationBook()
    cash = evidence(25_000_000)
    first = first_book.reserve(cash, "condition-A", current_identity=IDENTITY_A)
    with pytest.raises(CashReservationError, match="INSUFFICIENT"):
        second_book.reserve(cash, "condition-B", current_identity=IDENTITY_A)
    assert first.state is ReservationState.ACTIVE
    assert second_book.effective_available_micro(cash) == 0


def test_cross_book_same_identity_and_market_reuses_one_reservation():
    first_book, second_book = MICashReservationBook(), MICashReservationBook()
    cash = evidence(25_000_000)
    first = first_book.reserve(cash, "condition-A", current_identity=IDENTITY_A)
    assert second_book.reserve(cash, "condition-A", current_identity=IDENTITY_A) == first
    assert second_book.effective_available_micro(cash) == 0


def test_cross_book_shared_funder_different_signers_and_independent_funders():
    first_book, second_book = MICashReservationBook(), MICashReservationBook()
    first_book.reserve(evidence(40_000_000), "condition-A", current_identity=IDENTITY_A)
    with pytest.raises(CashReservationError, match="INSUFFICIENT"):
        second_book.reserve(evidence(40_000_000, IDENTITY_SAME_FUNDER), "condition-B",
                            current_identity=IDENTITY_SAME_FUNDER)
    assert second_book.effective_available_micro(evidence(40_000_000)) == 15_000_000
    other = second_book.reserve(evidence(25_000_000, IDENTITY_B), "condition-B",
                                current_identity=IDENTITY_B)
    assert other.state is ReservationState.ACTIVE
    assert first_book.effective_available_micro(evidence(25_000_000, IDENTITY_B)) == 0


def test_cross_book_different_funders_each_reserve_their_own_25():
    first_book, second_book = MICashReservationBook(), MICashReservationBook()
    first = first_book.reserve(evidence(25_000_000), "condition-A",
                               current_identity=IDENTITY_A)
    second = second_book.reserve(evidence(25_000_000, IDENTITY_B), "condition-B",
                                 current_identity=IDENTITY_B)
    assert first.state is ReservationState.ACTIVE
    assert second.state is ReservationState.ACTIVE
    assert first_book.effective_available_micro(evidence(25_000_000)) == 0
    assert second_book.effective_available_micro(evidence(25_000_000, IDENTITY_B)) == 0


def test_cross_book_release_checks_owner_and_old_id_cannot_release_new_record():
    first_book, second_book = MICashReservationBook(), MICashReservationBook()
    cash = evidence(25_000_000)
    first = first_book.reserve(cash, "condition-A", current_identity=IDENTITY_A)
    with pytest.raises(CashReservationError, match="OWNERSHIP"):
        second_book.release(first.reservation_id, current_identity=IDENTITY_SAME_FUNDER,
                            condition_id="condition-A")
    assert second_book.effective_available_micro(cash) == 0
    assert second_book.release(first.reservation_id, current_identity=IDENTITY_A,
                               condition_id="condition-A").state is ReservationState.RELEASED
    assert first_book.effective_available_micro(cash) == 25_000_000
    second = second_book.reserve(cash, "condition-A", current_identity=IDENTITY_A)
    assert second.reservation_id != first.reservation_id
    first_book.release(first.reservation_id, current_identity=IDENTITY_A,
                       condition_id="condition-A")
    assert first_book.effective_available_micro(cash) == 0


def test_cross_book_concurrent_reserves_have_one_process_lock():
    first_book, second_book = MICashReservationBook(), MICashReservationBook()
    cash = evidence(25_000_000)

    def attempt(item):
        book, market = item
        try:
            return book.reserve(cash, market, current_identity=IDENTITY_A)
        except CashReservationError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, (
            (first_book, "condition-A"), (second_book, "condition-B"),
        )))
    assert sum(record is not None for record in results) == 1
    assert first_book.effective_available_micro(cash) == 0
    assert second_book.effective_available_micro(cash) == 0


class FakeOfficialGateway:
    _paper = False
    address = ADDRESS_A
    funder = ADDRESS_A
    creds = object()

    def __init__(self, balance="25000000", orders=None):
        self.balance = balance
        self.orders = [] if orders is None else orders

    async def open_orders(self):
        return self.orders

    async def balance_allowance(self):
        return {"balance": self.balance, "allowances": {}}


CONFIG = SimpleNamespace(
    secrets=SimpleNamespace(pk=KEY_A, browser_address=ADDRESS_A),
    wallet=SimpleNamespace(signature_type=0),
)
REQUEST = LiveAuthorizationRequest(
    explicit_write_mode=True, armed=True, live_environment=True,
    private_key=KEY_A, configured_funder=ADDRESS_A, signature_type=0,
    connected_signer=ADDRESS_A, connected_funder=ADDRESS_A, authenticated=True,
)
AUTHORIZATION = authorize_live_write(REQUEST)


@pytest.mark.asyncio
async def test_official_balance_is_admitted_only_when_account_has_zero_open_orders():
    gateway = FakeOfficialGateway()
    snapshot = await read_confirmed_spendable_collateral(CONFIG, gateway, AUTHORIZATION)
    assert snapshot == evidence(25_000_000)
    gateway.orders = [object()]  # Any market: account-wide unresolved reservation.
    with pytest.raises(MICashReadinessError, match="ACCOUNT_OPEN_ORDERS_UNRESOLVED"):
        await read_confirmed_spendable_collateral(CONFIG, gateway, AUTHORIZATION)


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [None, "24999999.9", "nan", -1, 25_000_000.1])
async def test_unparseable_or_imprecise_official_balance_fails_closed(raw):
    with pytest.raises(MICashReadinessError):
        await read_confirmed_spendable_collateral(CONFIG, FakeOfficialGateway(raw), AUTHORIZATION)


@pytest.mark.asyncio
async def test_official_read_failure_and_identity_change_fail_closed():
    class FailedGateway(FakeOfficialGateway):
        async def open_orders(self):
            raise RuntimeError("offline fake read failure")

    with pytest.raises(MICashReadinessError, match="OFFICIAL_CASH_READ_FAILED"):
        await read_confirmed_spendable_collateral(CONFIG, FailedGateway(), AUTHORIZATION)
    with pytest.raises(MICashReadinessError, match="AUTHORIZATION_REQUIRED"):
        await read_confirmed_spendable_collateral(
            CONFIG, FakeOfficialGateway(), authorize_live_write(replace(REQUEST, armed=False)),
        )
    gateway = FakeOfficialGateway()
    gateway.address = ADDRESS_B
    gateway.funder = ADDRESS_B
    with pytest.raises(MICashReadinessError, match="IDENTITY_MISMATCH"):
        await read_confirmed_spendable_collateral(CONFIG, gateway, AUTHORIZATION)


@pytest.mark.asyncio
async def test_identity_change_during_official_reads_fails_closed():
    class ChangingGateway(FakeOfficialGateway):
        async def open_orders(self):
            self.address = ADDRESS_B
            self.funder = ADDRESS_B
            return []

    with pytest.raises(MICashReadinessError, match="IDENTITY_MISMATCH"):
        await read_confirmed_spendable_collateral(CONFIG, ChangingGateway(), AUTHORIZATION)
