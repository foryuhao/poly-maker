"""The sweeper tests use a wallet-state fake and never make network writes."""

from __future__ import annotations

import fcntl
import json
from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from eth_account import Account
from polymarket._internal.actions.relayer.positions import normalize_market_position_context
from polymarket._internal.environment import get_environment_config
from polymarket.environments import PRODUCTION
from polymarket.errors import UserInputError
from polymarket.models.gamma.market import Market
from polymarket.transactions import SyncGaslessTransactionHandle

import polymaker.settlement_sweep as module
from polymaker.config import Config, PathsConfig, Secrets
from polymaker.settlement_sweep import (
    PolymarketSettlementBackend,
    Position,
    SettlementMarketContext,
    resolve_settlement_context,
    sweep,
)

WALLET = "0x1111111111111111111111111111111111111111"


def cfg(tmp_path: Path) -> Config:
    return Config(paths=PathsConfig(log_dir=str(tmp_path)),
                  secrets=Secrets(BROWSER_ADDRESS=WALLET))


def pos(yes: int, no: int, *, resolved: bool = False,
        payouts: tuple[Decimal, Decimal] | None = None,
        protocol: str = "ctf", neg: bool = False,
        labels: tuple[str, str] = ("Yes", "No")) -> Position:
    env = get_environment_config(PRODUCTION)
    address = (env.neg_risk_adapter if neg else env.conditional_tokens)
    if protocol == "v2":
        address = env.position_manager
    context = SettlementMarketContext(
        "0x" + "ab" * 32, protocol, "101", "102", str(address), neg,
        str(env.neg_risk_collateral_adapter if neg else env.collateral_adapter)
        if protocol == "ctf" else None, "v1" if protocol == "ctf" else "v2",
        labels[0], labels[1])
    return Position(context, "market", yes * module.SCALE,
                    no * module.SCALE, resolved, resolved, payouts)


class FakeBackend:
    wallet = WALLET

    def __init__(self, positions: list[Position], *, collateral: int = 10) -> None:
        self.positions = {p.condition_id: p for p in positions}
        self.collateral = collateral * module.SCALE
        self.submitted: list[tuple[str, int]] = []
        self.fail_submit = False
        self.reject_submit = False
        self.fail_terminal = False
        self.confirm_without_state = False
        self.changed_before_submit = False
        self.connected = False

    def connect(self) -> None:
        self.connected = True

    def close(self) -> None:
        pass

    def collateral_raw(self) -> int:
        return self.collateral

    def discover(self) -> list[Position]:
        return [p for p in self.positions.values() if p.redeem_raw]

    def refresh(self, position: Position) -> Position:
        if self.changed_before_submit:
            self.changed_before_submit = False
            self.positions[position.condition_id] = replace(
                self.positions[position.condition_id], yes_raw=0)
        return self.positions[position.condition_id]

    def submit_merge(self, position: Position, amount_raw: int) -> SimpleNamespace:
        if self.reject_submit:
            raise UserInputError("pre-dispatch validation rejected")
        if self.fail_submit:
            raise OSError("uncertain HTTP submission")
        self.submitted.append(("MERGE", amount_raw))
        return SimpleNamespace(transaction_id="tx-1", transaction_hash="0xabc",
                               position=position, amount=amount_raw, kind="MERGE")

    def submit_redeem(self, position: Position) -> SimpleNamespace:
        if self.fail_submit:
            raise OSError("uncertain HTTP submission")
        self.submitted.append(("REDEEM", position.redeem_raw))
        return SimpleNamespace(transaction_id="tx-2", transaction_hash="0xdef",
                               position=position, amount=position.redeem_raw, kind="REDEEM")

    def wait(self, handle: SimpleNamespace) -> tuple[str, str, bool]:
        if self.fail_terminal:
            return handle.transaction_id, handle.transaction_hash, False
        if not self.confirm_without_state:
            p = self.positions[handle.position.condition_id]
            if handle.kind == "MERGE":
                self.positions[p.condition_id] = replace(
                    p, yes_raw=p.yes_raw - handle.amount, no_raw=p.no_raw - handle.amount)
                self.collateral += handle.amount
            else:
                self.collateral += p.expected_redeem_raw() or 0
                self.positions[p.condition_id] = replace(p, yes_raw=0, no_raw=0)
        return handle.transaction_id, handle.transaction_hash, True


def test_full_merge_and_capital_not_pnl(tmp_path):
    backend = FakeBackend([pos(5, 5)])
    row = sweep(cfg(tmp_path), backend, print_plan=lambda _: None)
    assert row["status"] == "CONFIRMED"
    assert backend.submitted == [("MERGE", 5 * module.SCALE)]
    assert row["starting_collateral"] == "10.000000"
    assert row["ending_collateral"] == "15.000000"
    assert row["merge_recovered"] == "5.000000"
    assert row["redeem_recovered"] == "0.000000"
    assert "pnl" not in row
    assert "Capital recovered is not trade PnL" in (
        Path(row["artifact_dir"]) / "settlement_report.md").read_text()
    assert len((Path(row["artifact_dir"]) / "settlement_report.md").read_text().splitlines()) < 150
    assert not (Path(row["artifact_dir"]) / "pending.json").exists()
    saved = json.loads((Path(row["artifact_dir"]) / "sweep.json").read_text())
    assert saved["actions"][0]["post_balances"]["yes_balance"] == "0.000000"
    assert saved["actions"][0]["terminal_status"] == "CONFIRMED"


def test_partial_merge_preserves_residual_and_second_run_idempotent(tmp_path):
    backend = FakeBackend([pos(5, 3)])
    first = sweep(cfg(tmp_path), backend, print_plan=lambda _: None)
    assert first["status"] == "CONFIRMED"
    assert backend.submitted == [("MERGE", 3 * module.SCALE)]
    assert first["positions_after"][0]["yes_balance"] == "2.000000"
    assert first["positions_after"][0]["no_balance"] == "0.000000"
    second = sweep(cfg(tmp_path), backend, print_plan=lambda _: None)
    assert second["status"] == "NOTHING_TO_DO"
    assert len(backend.submitted) == 1


def test_zero_balance_and_unresolved_residual_do_nothing(tmp_path):
    backend = FakeBackend([pos(0, 0), pos(2, 0)])
    row = sweep(cfg(tmp_path), backend, print_plan=lambda _: None)
    assert row["status"] == "NOTHING_TO_DO"
    assert not backend.submitted


def test_winning_and_zero_payout_redemption(tmp_path):
    for payouts, expected in [((Decimal(1), Decimal(0)), "2.000000"),
                              ((Decimal(0), Decimal(1)), "0.000000")]:
        backend = FakeBackend([pos(2, 0, resolved=True, payouts=payouts)])
        row = sweep(cfg(tmp_path), backend, print_plan=lambda _: None)
        assert row["status"] == "CONFIRMED"
        assert backend.submitted == [("REDEEM", 2 * module.SCALE)]
        assert row["redeem_recovered"] == expected
        again = sweep(cfg(tmp_path), backend, print_plan=lambda _: None)
        assert again["status"] == "NOTHING_TO_DO"
        assert len(backend.submitted) == 1


def test_resolved_partial_set_redeems_only(tmp_path):
    backend = FakeBackend([pos(5, 3, resolved=True,
                               payouts=(Decimal(1), Decimal(0)))])
    row = sweep(cfg(tmp_path), backend, print_plan=lambda _: None)
    assert backend.submitted == [("REDEEM", 8 * module.SCALE)]
    assert row["merge_recovered"] == "0.000000"
    assert row["redeem_recovered"] == "5.000000"
    assert row["status"] == "CONFIRMED"


def test_resolved_complete_set_redeems_only_and_preflight_has_one_queue(tmp_path):
    p = pos(5, 5, resolved=True, payouts=(Decimal(1), Decimal(0)),
            labels=("Up", "Down"))
    backend = FakeBackend([p])
    pre = module.preflight(cfg(tmp_path), backend, print_plan=lambda _: None)
    assert (pre["merge_candidates"], pre["redeem_candidates"]) == (0, 1)
    assert pre["positions_before"][0]["selected_action"] == "REDEEM"
    assert pre["positions_before"][0]["expected_redeem_proceeds"] == "5.000000"
    row = sweep(cfg(tmp_path), backend, print_plan=lambda _: None)
    assert backend.submitted == [("REDEEM", 10 * module.SCALE)]
    assert row["redeem_recovered"] == "5.000000"
    assert row["merge_recovered"] == "0.000000"


def test_unresolved_complete_set_merges_only(tmp_path):
    backend = FakeBackend([pos(5, 5, labels=("Up", "Down"))])
    pre = module.preflight(cfg(tmp_path), backend, print_plan=lambda _: None)
    assert (pre["merge_candidates"], pre["redeem_candidates"]) == (1, 0)
    row = sweep(cfg(tmp_path), backend, print_plan=lambda _: None)
    assert backend.submitted == [("MERGE", 5 * module.SCALE)]
    assert row["merge_recovered"] == "5.000000"


@pytest.mark.parametrize("payouts,expected", [
    ((Decimal(1), Decimal(0)), "5.000000"),
    ((Decimal(0), Decimal(1)), "0.000000"),
])
def test_resolved_one_sided_payouts(tmp_path, payouts, expected):
    backend = FakeBackend([pos(5, 0, resolved=True, payouts=payouts,
                               labels=("Up", "Down"))])
    row = sweep(cfg(tmp_path), backend, print_plan=lambda _: None)
    assert backend.submitted == [("REDEEM", 5 * module.SCALE)]
    assert row["redeem_recovered"] == expected
    assert row["positions_after"] == []


def test_multiple_conditions_each_select_one_mutation(tmp_path):
    resolved = pos(5, 5, resolved=True, payouts=(Decimal(1), Decimal(0)))
    unresolved = pos(5, 3)
    unresolved = replace(unresolved, context=replace(
        unresolved.context, condition_id="0x" + "cd" * 32), market="other")
    backend = FakeBackend([resolved, unresolved])
    pre = module.preflight(cfg(tmp_path), backend, print_plan=lambda _: None)
    assert (pre["merge_candidates"], pre["redeem_candidates"]) == (1, 1)
    row = sweep(cfg(tmp_path), backend, print_plan=lambda _: None)
    assert [a["action_type"] for a in row["actions"]] == ["REDEEM", "MERGE"]
    assert len({a["condition_id"] for a in row["actions"]}) == 2


def test_balance_change_before_dispatch_aborts(tmp_path):
    backend = FakeBackend([pos(5, 3)])
    backend.changed_before_submit = True
    row = sweep(cfg(tmp_path), backend, print_plan=lambda _: None)
    assert row["status"] == "MERGE_NOT_SUBMITTED"
    assert backend.submitted == []


def test_wallet_mismatch_and_unsupported_protocol_abort(tmp_path):
    backend = FakeBackend([pos(5, 5)])
    backend.wallet = "0x2222222222222222222222222222222222222222"
    assert sweep(cfg(tmp_path), backend, print_plan=lambda _: None)["status"] == "ABORTED"
    assert not backend.submitted
    backend = FakeBackend([pos(5, 5, protocol="unsupported")])
    # Live discovery rejects unsupported protocol before returning a candidate.
    def unsupported() -> list[Position]:
        raise RuntimeError("unsupported protocol")
    backend.discover = unsupported  # type: ignore[method-assign]
    assert sweep(cfg(tmp_path), backend, print_plan=lambda _: None)["status"] == "ABORTED"
    assert not backend.submitted


def test_resolution_disagreement_aborts(tmp_path):
    backend = FakeBackend([pos(2, 0, resolved=True, payouts=(Decimal(1), Decimal(0)))])
    def disagree() -> list[Position]:
        raise RuntimeError("resolution disagreement")
    backend.discover = disagree  # type: ignore[method-assign]
    assert sweep(cfg(tmp_path), backend, print_plan=lambda _: None)["status"] == "ABORTED"
    assert not backend.submitted


def test_submission_unknown_does_not_retry_and_blocks_later_sweep(tmp_path, monkeypatch):
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    backend = FakeBackend([pos(5, 5)])
    backend.fail_submit = True
    first = sweep(cfg(tmp_path), backend, print_plan=lambda _: None)
    assert first["actions"][0]["submission_status"] == "SUBMISSION_UNKNOWN"
    assert first["status"] == "SUBMISSION_UNKNOWN"
    assert first["actions"][0]["terminal_status"] == "UNKNOWN"
    assert (Path(first["artifact_dir"]) / "pending.json").exists()
    backend.fail_submit = False
    second = sweep(cfg(tmp_path), backend, print_plan=lambda _: None)
    assert second["status"] == "ABORTED"
    assert not backend.submitted


def test_submission_rejected_before_dispatch_is_failed(tmp_path):
    backend = FakeBackend([pos(5, 5)])
    backend.reject_submit = True
    row = sweep(cfg(tmp_path), backend, print_plan=lambda _: None)
    action = row["actions"][0]
    assert action["submission_status"] == "FAILED"
    assert action["terminal_status"] == "NOT_SUBMITTED"
    assert action["failure_phase"] == "SDK_MERGE_PRE_SUBMISSION_FAILED"
    assert action["exception_type"] == "UserInputError"
    assert action["safe_exception_message"] == "SDK rejected settlement input"
    assert action["verification_status"] == "NOT_VERIFIED"
    assert row["status"] == "MERGE_NOT_SUBMITTED"
    assert not (Path(row["artifact_dir"]) / "pending.json").exists()
    assert not backend.submitted


def test_sdk_terminal_wait_is_bounded_without_mutating_frozen_handle(tmp_path, monkeypatch):
    handle = SyncGaslessTransactionHandle("tx-id", "0xabc", None, 100, 10)
    seen = []

    def fake_wait(self):
        seen.append((self._max_polls, self._poll_delay_s))
        return SimpleNamespace(transaction_id="tx-id", transaction_hash="0xabc")

    monkeypatch.setattr(SyncGaslessTransactionHandle, "wait", fake_wait)
    result = PolymarketSettlementBackend(cfg(tmp_path)).wait(handle)
    assert result == ("tx-id", "0xabc", True)
    assert seen == [(24, 5)]
    assert (handle._max_polls, handle._poll_delay_s) == (100, 10)


def test_sdk_no_market_message_preserves_condition_without_secrets(tmp_path):
    p = pos(5, 5)
    backend = FakeBackend([p])
    message = f"No market found for condition {p.condition_id}"

    def reject(*args):
        raise UserInputError(message)

    backend.submit_merge = reject
    row = sweep(cfg(tmp_path), backend, print_plan=lambda _: None)
    action = row["actions"][0]
    assert row["status"] == "MERGE_NOT_SUBMITTED"
    assert action["safe_exception_message"] == message
    assert action["failure_phase"] == "SDK_MERGE_PRE_SUBMISSION_FAILED"


def test_failed_final_read_does_not_relabel_pre_submission_failure(tmp_path):
    backend = FakeBackend([pos(5, 5)])
    backend.reject_submit = True
    original_discover = backend.discover
    reads = 0

    def discover():
        nonlocal reads
        reads += 1
        if reads == 3:
            raise OSError("post-failure read unavailable")
        return original_discover()

    backend.discover = discover
    row = sweep(cfg(tmp_path), backend, print_plan=lambda _: None)
    assert row["status"] == "MERGE_NOT_SUBMITTED"
    assert row["actions"][0]["failure_phase"] == "SDK_MERGE_PRE_SUBMISSION_FAILED"


def test_failed_terminal_and_confirmed_post_state_mismatch(tmp_path, monkeypatch):
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    backend = FakeBackend([pos(5, 5)])
    backend.fail_terminal = True
    failed = sweep(cfg(tmp_path), backend, print_plan=lambda _: None)
    assert failed["actions"][0]["terminal_status"] == "FAILED"
    assert failed["actions"][0]["verification_status"] == "NOT_VERIFIED"
    backend = FakeBackend([pos(5, 5)])
    backend.confirm_without_state = True
    mismatch = sweep(cfg(tmp_path), backend, print_plan=lambda _: None)
    assert mismatch["actions"][0]["terminal_status"] == "CONFIRMED"
    assert mismatch["actions"][0]["verification_status"] == "INCOMPLETE_RECONCILIATION"
    assert mismatch["status"] == "MERGE_INCOMPLETE_RECONCILIATION"


def test_concurrent_lock_performs_no_transactions(tmp_path):
    backend = FakeBackend([pos(5, 5)])
    lock_path = tmp_path / "settlement" / "sweep.lock"
    lock_path.parent.mkdir(parents=True)
    with lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        row = sweep(cfg(tmp_path), backend, print_plan=lambda _: None)
        fcntl.flock(lock, fcntl.LOCK_UN)
    assert row["status"] == "LOCKED"
    assert not backend.connected
    assert not backend.submitted


def test_settlement_source_has_no_clob_order_mutation():
    source = Path(module.__file__).read_text()
    for forbidden in (".place_order(", ".post_order(", ".cancel_order(",
                      ".cancel_market_orders(", ".place_limit_order(",
                      ".place_market_order("):
        assert forbidden not in source


def market(*, version: str | None, neg: bool | None, ctf: tuple[str | None, str | None],
           v2: tuple[str | None, str | None] = (None, None),
           labels: tuple[str, str] = ("YES", "NO")) -> SimpleNamespace:
    yes = SimpleNamespace(label=labels[0], token_id=ctf[0], position_id=v2[0])
    no = SimpleNamespace(label=labels[1], token_id=ctf[1], position_id=v2[1])
    return SimpleNamespace(id=1, condition_id="0x" + "ab" * 32, slug="market",
                           version=SimpleNamespace(value=version) if version else None,
                           state=SimpleNamespace(neg_risk=neg, closed=False),
                           outcomes=SimpleNamespace(yes=yes, no=no))


def sdk_context(m: SimpleNamespace) -> object:
    env = get_environment_config(PRODUCTION)
    return normalize_market_position_context(
        m, context="test", collateral_adapter=env.collateral_adapter,
        neg_risk_collateral_adapter=env.neg_risk_collateral_adapter,
        conditional_tokens=env.conditional_tokens, neg_risk_adapter=env.neg_risk_adapter,
        position_manager=env.position_manager)


@pytest.mark.parametrize("m", [
    market(version="v1", neg=False, ctf=("101", "102")),
    market(version="v1", neg=True, ctf=("101", "102")),
    market(version="v2", neg=False, ctf=(None, None), v2=("201", "202")),
])
def test_context_matches_installed_sdk_0110(m):
    env = get_environment_config(PRODUCTION)
    ours = resolve_settlement_context(m, env)
    sdk = sdk_context(m)
    assert ours.protocol == sdk.protocol
    assert (ours.yes_id, ours.no_id) == tuple(map(str, sdk.token_ids))
    assert ours.position_erc1155_address.lower() == str(sdk.position_erc1155_address).lower()
    assert (ours.adapter_address or "").lower() == str(sdk.adapter_address or "").lower()
    if ours.protocol == "ctf":
        assert ours.negative_risk == sdk.neg_risk


def test_pinned_sdk_keeps_raw_binary_order_and_rejects_three_outcomes():
    raw = {"id": "1", "conditionId": "0x" + "ab" * 32,
           "outcomes": ["Up", "Down"], "clobTokenIds": ["101", "102"]}
    m = Market.model_validate(raw)
    assert (m.outcomes.yes.label, m.outcomes.no.label) == ("Up", "Down")
    assert (str(m.outcomes.yes.token_id), str(m.outcomes.no.token_id)) == ("101", "102")
    with pytest.raises(ValueError, match="Expected binary market outcomes"):
        Market.model_validate({**raw, "outcomes": ["Up", "Down", "Flat"]})


@pytest.mark.parametrize("labels", [
    ("Up", "Down"), ("Above", "Below"), ("Yes", "No"), ("Down", "Up"),
])
def test_arbitrary_binary_labels_preserve_sdk_slot_order(labels):
    m = market(version="v1", neg=False, ctf=("101", "102"), labels=labels)
    context = resolve_settlement_context(m, get_environment_config(PRODUCTION))
    assert (context.outcome0_label, context.outcome1_label) == labels
    assert (context.yes_id, context.no_id) == ("101", "102")
    assert sdk_context(m).token_ids == ("101", "102")


def test_extra_or_duplicate_outcomes_fail_closed():
    m = market(version="v1", neg=False, ctf=("101", "102"))
    m.outcomes.third = SimpleNamespace(label="Flat", token_id="103", position_id=None)
    with pytest.raises(RuntimeError, match="expected exactly two outcomes"):
        resolve_settlement_context(m, get_environment_config(PRODUCTION))
    del m.outcomes.third
    m.outcomes.no.label = "YES"
    with pytest.raises(RuntimeError, match="OUTCOME_MAPPING_CONFLICT"):
        resolve_settlement_context(m, get_environment_config(PRODUCTION))


def test_up_down_backend_discovery_uses_ordered_ids_and_labels(tmp_path):
    m = market(version="v1", neg=False, ctf=("101", "102"), labels=("Up", "Down"))
    backend, chain = backend_fixture(tmp_path, m)
    address = str(backend.environment.conditional_tokens).lower()
    chain.balances[(address, 101)] = 5 * module.SCALE
    chain.balances[(address, 102)] = 3 * module.SCALE
    backend.client.rows.append(SimpleNamespace(
        slug="market", wallet=WALLET, negative_risk=False,
        condition_id=m.condition_id, current_size=Decimal(3), asset_id="102",
        outcome="Down", outcome_index=1, opposite_outcome="Up",
        opposite_asset_id="101", redeemable=False))
    found = backend.discover()[0]
    assert found.merge_raw == 3 * module.SCALE
    recorded = module.position_row(found)
    assert [(o["index"], o["label"], o["asset_id"]) for o in recorded["outcomes"]] == [
        (0, "Up", "101"), (1, "Down", "102")]
    assert recorded["residual_outcome_index"] == 0
    assert recorded["residual_label"] == "Up" and recorded["residual_balance"] == "2.000000"


def test_outcome_index_or_label_disagreement_fails_closed(tmp_path):
    m = market(version="v1", neg=False, ctf=("101", "102"), labels=("Down", "Up"))
    backend, _ = backend_fixture(tmp_path, m)
    backend.client.rows[0].outcome_index = 1
    with pytest.raises(RuntimeError, match="OUTCOME_MAPPING_CONFLICT: index differs"):
        backend._descriptor(m.condition_id, backend.client.rows)
    backend.client.rows[0].outcome_index = 0
    backend.client.rows[0].outcome = "Up"
    with pytest.raises(RuntimeError, match="OUTCOME_MAPPING_CONFLICT: label differs"):
        backend._descriptor(m.condition_id, backend.client.rows)


@pytest.mark.parametrize("payouts,expected", [
    ((Decimal(1), Decimal(0)), 5), ((Decimal(0), Decimal(1)), 3),
])
def test_up_down_redeem_payout_follows_slot_index(payouts, expected):
    p = pos(5, 3, resolved=True, payouts=payouts, labels=("Up", "Down"))
    assert p.expected_redeem_raw() == expected * module.SCALE


def test_partial_up_down_merge_reports_actual_residual_label(tmp_path):
    backend = FakeBackend([pos(5, 3, labels=("Up", "Down"))])
    row = sweep(cfg(tmp_path), backend, print_plan=lambda _: None)
    assert backend.submitted == [("MERGE", 3 * module.SCALE)]
    assert row["positions_after"][0]["residual_label"] == "Up"
    assert row["positions_after"][0]["residual_balance"] == "2.000000"
    report = (Path(row["artifact_dir"]) / "settlement_report.md").read_text()
    assert "Up (index 0): 5.000000" in report
    assert "Down (index 1): 3.000000" in report


@pytest.mark.parametrize("m", [
    market(version="v2", neg=False, ctf=("101", "102")),
    market(version="v1", neg=False, ctf=("101", "102"), v2=("201", "202")),
    market(version="v1", neg=False, ctf=("101", None), v2=("201", "202")),
    market(version="v2", neg=False, ctf=(None, None), v2=("201", None)),
    market(version="v1", neg=None, ctf=("101", "102")),
])
def test_conflicting_or_incomplete_metadata_aborts(m):
    with pytest.raises(RuntimeError, match="PROTOCOL_METADATA_CONFLICT"):
        resolve_settlement_context(m, get_environment_config(PRODUCTION))


def test_reviewer_contradictory_version_sdk_ctf_but_sweeper_aborts():
    m = market(version="v2", neg=False, ctf=("101", "102"), v2=("201", "202"))
    assert sdk_context(m).protocol == "ctf"  # pinned SDK precedence
    with pytest.raises(RuntimeError, match="PROTOCOL_METADATA_CONFLICT"):
        resolve_settlement_context(m, get_environment_config(PRODUCTION))


def test_missing_condition_invalid_id_and_zero_contract_abort():
    env = get_environment_config(PRODUCTION)
    m = market(version="v1", neg=False, ctf=("101", "102"))
    m.condition_id = None
    with pytest.raises(RuntimeError, match="invalid condition_id"):
        resolve_settlement_context(m, env)
    m.condition_id = "0x" + "ab" * 32
    m.outcomes.yes.token_id = "١٠١"
    with pytest.raises(RuntimeError, match="invalid outcome IDs"):
        resolve_settlement_context(m, env)
    m.outcomes.yes.token_id = "101"
    broken_env = SimpleNamespace(**asdict(env))
    broken_env.conditional_tokens = "0x" + "0" * 40
    with pytest.raises(RuntimeError, match="invalid contract address"):
        resolve_settlement_context(m, broken_env)
    broken_env.conditional_tokens = None
    with pytest.raises(RuntimeError, match="missing contract address"):
        resolve_settlement_context(m, broken_env)


class OnchainCall:
    def __init__(self, value: int) -> None:
        self.value = value

    def call(self) -> int:
        return self.value


class MockContract:
    def __init__(self, chain: MockChain, address: str) -> None:
        self.chain, self.address = chain, address.lower()
        self.functions = self

    def balanceOf(self, *args: object) -> OnchainCall:
        token = int(args[1]) if len(args) == 2 else None
        return OnchainCall(self.chain.balances.get((self.address, token), 0))

    def payoutDenominator(self, condition: bytes) -> OnchainCall:
        return OnchainCall(0)


class MockChain:
    def __init__(self) -> None:
        self.balances: dict[tuple[str, int | None], int] = {}
        self.reads: list[str] = []
        self.eth = self

    def contract(self, *, address: str, abi: object) -> MockContract:
        self.reads.append(address.lower())
        return MockContract(self, address)


class BackendClient:
    def __init__(self, m: SimpleNamespace, rows: list[SimpleNamespace]) -> None:
        self.m, self.rows = m, rows

    def get_market(self, **kwargs: object) -> SimpleNamespace:
        return self.m

    def list_positions(self, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(iter_items=lambda: iter(self.rows))

    def get_resolutions(self, **kwargs: object) -> list[object]:
        return []


def backend_fixture(tmp_path: Path, m: SimpleNamespace) -> tuple[PolymarketSettlementBackend, MockChain]:
    backend = PolymarketSettlementBackend(cfg(tmp_path))
    backend.environment = get_environment_config(PRODUCTION)
    chain = MockChain()
    backend.w3 = chain
    row = SimpleNamespace(slug="market", wallet=WALLET, negative_risk=m.state.neg_risk,
                          condition_id=m.condition_id, current_size=Decimal(5),
                          asset_id=m.outcomes.yes.token_id or m.outcomes.yes.position_id,
                          outcome=m.outcomes.yes.label, outcome_index=0,
                          opposite_outcome=m.outcomes.no.label,
                          opposite_asset_id=m.outcomes.no.token_id or m.outcomes.no.position_id,
                          redeemable=False)
    backend.client = BackendClient(m, [row])
    return backend, chain


def test_negative_risk_real_backend_reads_adapter_and_verifies_post_merge(tmp_path):
    m = market(version="v1", neg=True, ctf=("101", "102"))
    backend, chain = backend_fixture(tmp_path, m)
    env = backend.environment
    adapter = str(env.neg_risk_adapter).lower()
    standard = str(env.conditional_tokens).lower()
    chain.balances[(adapter, 101)] = 5 * module.SCALE
    chain.balances[(adapter, 102)] = 5 * module.SCALE
    chain.balances[(standard, 101)] = 0
    chain.balances[(standard, 102)] = 0
    initial = backend.discover()[0]
    assert initial.protocol == "ctf"
    assert initial.context.position_erc1155_address.lower() == adapter
    assert initial.merge_raw == 5 * module.SCALE
    assert standard not in chain.reads[:2]
    chain.balances[(adapter, 101)] = 0
    chain.balances[(adapter, 102)] = 0
    chain.balances[(str(env.collateral_token).lower(), None)] = 15 * module.SCALE
    post, collateral, verified = module._verify(
        backend, initial, 5 * module.SCALE, 10 * module.SCALE,
        "MERGE", 5 * module.SCALE, retries=1)
    assert verified
    assert post.yes_raw == post.no_raw == 0
    assert collateral == 15 * module.SCALE


def test_negative_risk_redeem_verification_reads_adapter(tmp_path):
    m = market(version="v1", neg=True, ctf=("101", "102"))
    backend, chain = backend_fixture(tmp_path, m)
    env = backend.environment
    adapter = str(env.neg_risk_adapter).lower()
    chain.balances[(adapter, 101)] = 5 * module.SCALE
    before = backend.discover()[0]
    chain.balances[(adapter, 101)] = 0
    chain.balances[(str(env.collateral_token).lower(), None)] = 15 * module.SCALE
    post, _, verified = module._verify(
        backend, before, 5 * module.SCALE, 10 * module.SCALE,
        "REDEEM", 5 * module.SCALE, retries=1)
    assert verified and post.yes_raw == 0


@pytest.mark.parametrize("neg,ctf,v2,expected", [
    (False, ("101", "102"), (None, None), "conditional_tokens"),
    (True, ("101", "102"), (None, None), "neg_risk_adapter"),
    (False, (None, None), ("201", "202"), "position_manager"),
])
def test_real_backend_balance_contract_selection(tmp_path, neg, ctf, v2, expected):
    m = market(version="v1" if ctf[0] else "v2", neg=neg, ctf=ctf, v2=v2)
    backend, chain = backend_fixture(tmp_path, m)
    context = resolve_settlement_context(m, backend.environment)
    chain.balances[(str(getattr(backend.environment, expected)).lower(), int(context.yes_id))] = 7
    assert backend._balance(context, context.yes_id) == 7
    assert chain.reads[-1] == str(getattr(backend.environment, expected)).lower()


def test_mixed_negative_risk_discovered_rows_abort(tmp_path):
    m = market(version="v1", neg=True, ctf=("101", "102"))
    backend, _ = backend_fixture(tmp_path, m)
    backend.client.rows.append(SimpleNamespace(slug="market", wallet=WALLET,
        negative_risk=False, asset_id="102", outcome="NO", redeemable=False))
    with pytest.raises(RuntimeError, match="negative-risk metadata mismatch"):
        backend._descriptor(m.condition_id, backend.client.rows)


def test_sdk_dispatch_route_conflict_aborts_before_merge(tmp_path):
    m = market(version="v1", neg=True, ctf=("101", "102"))
    backend, _ = backend_fixture(tmp_path, m)
    context = resolve_settlement_context(m, backend.environment)
    position = Position(context, "market", 5 * module.SCALE, 5 * module.SCALE, False, False)
    calls = []
    backend.client._resolve_market_position_context = lambda **kw: SimpleNamespace(
        condition_id=context.condition_id, protocol="v2", token_ids=("201", "202"),
        position_erc1155_address=backend.environment.position_manager,
        neg_risk=None, adapter_address=None)
    backend.client.merge_positions = lambda **kw: calls.append(kw)
    with pytest.raises(RuntimeError, match="SDK dispatch route differs"):
        backend.submit_merge(position, 5 * module.SCALE)
    assert not calls


def test_canonical_live_env_aliases_feed_favorite_and_settlement(monkeypatch):
    key = "0x" + "11" * 32
    funder = WALLET
    for name in ("PK", "BROWSER_ADDRESS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", key)
    monkeypatch.setenv("POLYMARKET_WALLET_ADDRESS", funder)
    config = Config.load(load_env=False)
    assert config.secrets.pk == key
    assert config.secrets.browser_address == funder
    assert Account.from_key(config.secrets.pk).address == Account.from_key(key).address
    assert PolymarketSettlementBackend(config).wallet == funder


def test_missing_canonical_wallet_credentials_abort_before_network(tmp_path):
    backend = PolymarketSettlementBackend(Config(
        paths=PathsConfig(log_dir=str(tmp_path)), secrets=Secrets(PK="", BROWSER_ADDRESS="")))
    with pytest.raises(RuntimeError, match="CANONICAL_PRIVATE_KEY_MISSING"):
        backend.connect()
    backend = PolymarketSettlementBackend(Config(
        paths=PathsConfig(log_dir=str(tmp_path)),
        secrets=Secrets(PK="0x" + "11" * 32, BROWSER_ADDRESS="")))
    with pytest.raises(RuntimeError, match="CANONICAL_FUNDER_MISSING"):
        backend.connect()


def test_safe_builder_auth_is_required_before_sdk_or_network(tmp_path):
    backend = PolymarketSettlementBackend(Config(
        paths=PathsConfig(log_dir=str(tmp_path)),
        secrets=Secrets(PK="0x" + "11" * 32, BROWSER_ADDRESS=WALLET,
                        POLY_BUILDER_KEY="", POLY_BUILDER_SECRET="",
                        POLY_BUILDER_PASSPHRASE="")))
    with pytest.raises(RuntimeError, match="BUILDER_AUTH_MISSING"):
        backend.connect()


def test_preflight_reads_and_artifacts_without_mutation(tmp_path):
    backend = FakeBackend([pos(5, 3, labels=("Up", "Down"))])
    backend.indexed_positive_rows = 2
    backend.signer = "0x" + "22" * 20
    backend.wallet_type = "GNOSIS_SAFE"
    backend.auth_mode = "BUILDER_RELAYED"
    backend.submit_merge = lambda *args: pytest.fail("preflight attempted MERGE")
    backend.submit_redeem = lambda *args: pytest.fail("preflight attempted REDEEM")
    messages = []
    row = module.preflight(cfg(tmp_path), backend, print_plan=messages.append)
    assert row["status"] == "PREFLIGHT_PASS"
    assert row["mode"] == "PREFLIGHT_ONLY" and row["mutation_count"] == 0
    assert row["merge_candidates"] == 1 and row["redeem_candidates"] == 0
    assert row["positive_position_rows"] == 2 and row["validated_conditions"] == 1
    assert row["unresolved_residuals"][0]["label"] == "Up"
    assert not backend.submitted
    saved = json.loads((Path(row["artifact_dir"]) / "sweep.json").read_text())
    assert saved["mutation_count"] == 0 and saved["positions_before"] == row["positions_before"]
    report = (Path(row["artifact_dir"]) / "settlement_report.md").read_text()
    assert "mutation count: 0" in report
    assert "outcome 0: Up" in report and "outcome 1: Down" in report
    assert any("No mutation: CONFIRMED" in message for message in messages)


def test_preflight_discovery_conflict_keeps_candidate_counts_unknown(tmp_path):
    backend = FakeBackend([pos(5, 5)])
    backend.discover = lambda: (_ for _ in ()).throw(
        RuntimeError("PROTOCOL_METADATA_CONFLICT: non-binary outcome mapping"))
    row = module.preflight(cfg(tmp_path), backend, print_plan=lambda _: None)
    assert row["status"] == "PREFLIGHT_ABORTED"
    assert row["gates"]["position_discovery"] == "FAIL"
    assert row["merge_candidates"] is None and row["redeem_candidates"] is None
    assert row["mutation_count"] == 0 and not backend.submitted
    assert row["anomalies"] == ["PROTOCOL_METADATA_CONFLICT: non-binary outcome mapping"]


def test_connect_validates_safe_owner_and_sdk_identity(tmp_path, monkeypatch):
    import polymarket
    import web3

    real_web3 = web3.Web3
    key = "0x" + "11" * 32
    signer = Account.from_key(key).address
    owner = [signer]
    sdk_wallet = [WALLET]
    sdk_signer = [signer]
    sdk_calls = []

    class FakeSafeFunctions:
        def getOwners(self):
            return SimpleNamespace(call=lambda: owner)

        def getThreshold(self):
            return SimpleNamespace(call=lambda: 1)

    class FakeEth:
        chain_id = 137

        def get_code(self, address):
            return b"\x01"

        def contract(self, **kwargs):
            return SimpleNamespace(functions=FakeSafeFunctions())

    class FakeWeb3:
        eth = FakeEth()
        middleware_onion = SimpleNamespace(inject=lambda *args, **kwargs: None)
        HTTPProvider = staticmethod(lambda *args, **kwargs: None)
        is_address = staticmethod(real_web3.is_address)
        to_checksum_address = staticmethod(real_web3.to_checksum_address)

        def __init__(self, provider):
            pass

    def fake_create(**kwargs):
        sdk_calls.append(kwargs)
        return SimpleNamespace(wallet=sdk_wallet[0], signer=sdk_signer[0],
                               wallet_type="GNOSIS_SAFE", close=lambda: None)

    monkeypatch.setattr(web3, "Web3", FakeWeb3)
    monkeypatch.setattr(polymarket.SecureClient, "create", staticmethod(fake_create))
    config = Config(paths=PathsConfig(log_dir=str(tmp_path)), secrets=Secrets(
        PK=key, BROWSER_ADDRESS=WALLET, POLY_BUILDER_KEY="builder-key",
        POLY_BUILDER_SECRET="builder-secret", POLY_BUILDER_PASSPHRASE="builder-passphrase"))
    backend = PolymarketSettlementBackend(config)
    backend.connect()
    assert backend.signer == signer and backend.wallet == WALLET
    assert backend.wallet_type == "GNOSIS_SAFE"
    assert sdk_calls[0]["private_key"] == config.secrets.pk
    assert sdk_calls[0]["wallet"] == config.secrets.browser_address
    owner[:] = ["0x" + "33" * 20]
    with pytest.raises(RuntimeError, match="Safe owner"):
        PolymarketSettlementBackend(config).connect()
    assert len(sdk_calls) == 1
    owner[:] = [signer]
    sdk_wallet[:] = ["0x" + "44" * 20]
    with pytest.raises(RuntimeError, match="SDK wallet differs"):
        PolymarketSettlementBackend(config).connect()
    sdk_wallet[:] = [WALLET]
    sdk_signer[:] = ["0x" + "55" * 20]
    with pytest.raises(RuntimeError, match="SDK signer differs"):
        PolymarketSettlementBackend(config).connect()


def test_cli_preflight_cannot_reach_live_sweep(monkeypatch):
    from typer.testing import CliRunner

    from polymaker.cli import app

    def forbidden(*args, **kwargs):
        pytest.fail("--preflight-only reached live sweep")

    monkeypatch.setattr(module, "sweep", forbidden)
    monkeypatch.setattr(module, "preflight", lambda *args, **kwargs: {
        "status": "PREFLIGHT_PASS", "artifact_dir": "/tmp/preflight-test"})
    result = CliRunner().invoke(app, ["settlement-sweep", "--preflight-only"])
    assert result.exit_code == 0
    assert "PREFLIGHT_PASS" in result.output
