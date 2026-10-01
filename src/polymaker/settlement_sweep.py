"""One-shot, fail-closed settlement of funder-owned outcome tokens.

The only mutation methods used here are the official SDK's merge_positions and
redeem_positions. Discovery is an index; ERC-1155/ERC-20 on-chain balances are
the authority at dispatch and verification.
"""

from __future__ import annotations

import fcntl
import json
import os
import threading
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from polymaker.config import Config

SCALE = 1_000_000
SCHEMA_VERSION = 1
BALANCE_ABI = [{"name": "balanceOf", "type": "function", "stateMutability": "view",
                "inputs": [{"name": "owner", "type": "address"},
                           {"name": "id", "type": "uint256"}],
                "outputs": [{"type": "uint256"}]}]
ERC20_ABI = [{"name": "balanceOf", "type": "function", "stateMutability": "view",
              "inputs": [{"name": "owner", "type": "address"}],
              "outputs": [{"type": "uint256"}]}]
PAYOUT_ABI = [
    {"name": "payoutDenominator", "type": "function", "stateMutability": "view",
     "inputs": [{"type": "bytes32"}], "outputs": [{"type": "uint256"}]},
    {"name": "payoutNumerators", "type": "function", "stateMutability": "view",
     "inputs": [{"type": "bytes32"}, {"type": "uint256"}],
     "outputs": [{"type": "uint256"}]},
]
SAFE_ABI = [{"name": "getOwners", "type": "function", "stateMutability": "view",
             "inputs": [], "outputs": [{"type": "address[]"}]},
            {"name": "getThreshold", "type": "function", "stateMutability": "view",
             "inputs": [], "outputs": [{"type": "uint256"}]}]


def units(raw: int | None) -> str | None:
    return None if raw is None else f"{Decimal(raw) / SCALE:.6f}"


def now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class SettlementMarketContext:
    """One route; yes/no IDs are SDK slots 0/1, not literal label meanings."""

    condition_id: str
    protocol: str
    yes_id: str
    no_id: str
    position_erc1155_address: str
    negative_risk: bool
    adapter_address: str | None
    metadata_version: str | None
    outcome0_label: str = "Yes"
    outcome1_label: str = "No"


def resolve_settlement_context(market: Any, environment: Any) -> SettlementMarketContext:
    """Mirror polymarket-client 0.11.0 ID precedence, then reject contradictions."""
    from web3 import Web3

    condition = str(market.condition_id or "")
    if (not condition.startswith("0x") or len(condition) != 66 or
            any(c not in "0123456789abcdefABCDEF" for c in condition[2:])):
        raise RuntimeError("PROTOCOL_METADATA_CONFLICT: invalid condition_id")
    outcomes = market.outcomes
    if (set(vars(outcomes)) != {"yes", "no"} or
            getattr(outcomes, "model_extra", None)):
        raise RuntimeError("PROTOCOL_METADATA_CONFLICT: expected exactly two outcomes")
    labels = (outcomes.yes.label, outcomes.no.label)
    if (not all(isinstance(label, str) and label.strip() for label in labels) or
            labels[0].strip().casefold() == labels[1].strip().casefold()):
        raise RuntimeError("OUTCOME_MAPPING_CONFLICT: missing or duplicate labels")
    token_ids = (outcomes.yes.token_id, outcomes.no.token_id)
    position_ids = (outcomes.yes.position_id, outcomes.no.position_id)
    if (token_ids[0] is None) != (token_ids[1] is None):
        raise RuntimeError("PROTOCOL_METADATA_CONFLICT: incomplete CTF token IDs")
    if (position_ids[0] is None) != (position_ids[1] is None):
        raise RuntimeError("PROTOCOL_METADATA_CONFLICT: incomplete V2 position IDs")
    has_ctf = token_ids[0] is not None
    has_v2 = position_ids[0] is not None
    # SDK 0.11.0 prefers CTF when both are present. The extra V2 identifiers
    # make independent settlement metadata ambiguous, so fail before any read.
    if has_ctf and has_v2:
        raise RuntimeError("PROTOCOL_METADATA_CONFLICT: both identifier families present")
    if not has_ctf and not has_v2:
        raise RuntimeError("PROTOCOL_METADATA_CONFLICT: no outcome IDs")
    protocol = "ctf" if has_ctf else "v2"
    version = market.version.value if market.version is not None else None
    if version is not None and version != ("v1" if protocol == "ctf" else "v2"):
        raise RuntimeError("PROTOCOL_METADATA_CONFLICT: version disagrees with identifiers")
    negative_risk = market.state.neg_risk
    if not isinstance(negative_risk, bool):
        raise RuntimeError("PROTOCOL_METADATA_CONFLICT: missing negative-risk flag")
    yes, no = token_ids if has_ctf else position_ids
    ids = (str(yes), str(no))
    if (ids[0] == ids[1] or any(not value.isascii() or not value.isdigit() or
                                not 0 < int(value) < 2**256
                                for value in ids)):
        raise RuntimeError("PROTOCOL_METADATA_CONFLICT: invalid outcome IDs")
    if protocol == "ctf":
        address = (environment.neg_risk_adapter if negative_risk
                   else environment.conditional_tokens)
        adapter = (environment.neg_risk_collateral_adapter if negative_risk
                   else environment.collateral_adapter)
    else:
        address, adapter = environment.position_manager, None
    if address is None or (protocol == "ctf" and adapter is None):
        raise RuntimeError("PROTOCOL_METADATA_CONFLICT: missing contract address")
    for candidate in (address, adapter):
        if candidate is not None and (not Web3.is_address(candidate) or
                                      int(candidate, 16) == 0):
            raise RuntimeError("PROTOCOL_METADATA_CONFLICT: invalid contract address")
    return SettlementMarketContext(condition, protocol, ids[0], ids[1],
                                   Web3.to_checksum_address(address), negative_risk,
                                   Web3.to_checksum_address(adapter) if adapter else None,
                                   version, labels[0], labels[1])


def validate_position_mapping(row: Any, context: SettlementMarketContext) -> None:
    """Cross-check a Data API row against the SDK's ordered binary slots."""
    if str(row.condition_id).lower() != context.condition_id.lower():
        raise RuntimeError("OUTCOME_MAPPING_CONFLICT: condition differs")
    asset = str(row.asset_id)
    slots = ((context.yes_id, context.outcome0_label),
             (context.no_id, context.outcome1_label))
    if asset not in {slots[0][0], slots[1][0]}:
        raise RuntimeError("OUTCOME_MAPPING_CONFLICT: asset ID differs")
    index = 0 if asset == slots[0][0] else 1
    label = getattr(row, "outcome", None)
    if label is not None and str(label).strip().casefold() != slots[index][1].strip().casefold():
        raise RuntimeError("OUTCOME_MAPPING_CONFLICT: label differs")
    row_index = getattr(row, "outcome_index", None)
    if row_index is not None and row_index != index:
        raise RuntimeError("OUTCOME_MAPPING_CONFLICT: index differs")
    opposite_id = getattr(row, "opposite_asset_id", None)
    if opposite_id is not None and str(opposite_id) != slots[1 - index][0]:
        raise RuntimeError("OUTCOME_MAPPING_CONFLICT: opposite asset differs")
    opposite_label = getattr(row, "opposite_outcome", None)
    if (opposite_label is not None and
            str(opposite_label).strip().casefold() != slots[1 - index][1].strip().casefold()):
        raise RuntimeError("OUTCOME_MAPPING_CONFLICT: opposite label differs")


@dataclass(frozen=True)
class Position:
    context: SettlementMarketContext
    market: str
    yes_raw: int
    no_raw: int
    resolved: bool
    redeemable: bool
    payouts: tuple[Decimal, Decimal] | None = None
    market_closed: bool = False

    @property
    def condition_id(self) -> str:
        return self.context.condition_id

    @property
    def yes_token(self) -> str:
        return self.context.yes_id

    @property
    def no_token(self) -> str:
        return self.context.no_id

    @property
    def protocol(self) -> str:
        return self.context.protocol

    @property
    def negative_risk(self) -> bool:
        return self.context.negative_risk

    @property
    def merge_raw(self) -> int:
        return min(self.yes_raw, self.no_raw)

    @property
    def redeem_raw(self) -> int:
        return self.yes_raw + self.no_raw

    def expected_redeem_raw(self) -> int | None:
        if self.payouts is None:
            return None
        value = self.yes_raw * self.payouts[0] + self.no_raw * self.payouts[1]
        return int(value) if value == int(value) else None


def selected_action(position: Position) -> str | None:
    """Select one lifecycle mutation from authoritative settlement state."""
    if position.redeem_raw > 0 and position.resolved and position.redeemable:
        return "REDEEM"
    if not position.resolved and position.merge_raw > 0:
        return "MERGE"
    return None


class SweepBackend(Protocol):
    wallet: str

    def connect(self) -> None: ...
    def collateral_raw(self) -> int: ...
    def discover(self) -> list[Position]: ...
    def refresh(self, position: Position) -> Position: ...
    def submit_merge(self, position: Position, amount_raw: int) -> Any: ...
    def submit_redeem(self, position: Position) -> Any: ...
    def wait(self, handle: Any) -> tuple[str | None, str | None, bool]: ...
    def close(self) -> None: ...


class PolymarketSettlementBackend:
    """Official SDK writes; direct Polygon reads for funder-owned balances."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.wallet = cfg.secrets.browser_address
        self.client: Any = None
        self.w3: Any = None
        self.environment: Any = None
        self.signer: str | None = None
        self.wallet_type: str | None = None
        self.auth_mode: str | None = None
        self.indexed_positive_rows: int | None = None

    def connect(self) -> None:
        from eth_account import Account
        from polymarket import SecureClient
        from polymarket._internal.environment import get_environment_config
        from polymarket.auth import BuilderApiKey
        from polymarket.environments import PRODUCTION
        from web3 import Web3
        from web3.middleware import ExtraDataToPOAMiddleware

        sec = self.cfg.secrets
        if self.cfg.wallet.chain_id != 137:
            raise RuntimeError("POLYGON_CHAIN_MISMATCH")
        if not sec.pk:
            raise RuntimeError("CANONICAL_PRIVATE_KEY_MISSING")
        if not sec.browser_address:
            raise RuntimeError("CANONICAL_FUNDER_MISSING")
        # SDK 0.11.0 dispatches Safe/proxy settlement through the gasless
        # relayer, whose prepare_gasless_transaction requires an API key.
        if self.cfg.wallet.signature_type != 0 and not sec.has_builder_creds:
            raise RuntimeError("BUILDER_AUTH_MISSING")
        if not Web3.is_address(self.wallet):
            raise RuntimeError("configured funder address is invalid")
        rpc = sec.polygon_rpc or self.cfg.wallet.polygon_rpc
        w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 20}))
        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        if w3.eth.chain_id != 137:
            raise RuntimeError("RPC is not Polygon mainnet")
        signer = Account.from_key(sec.pk).address
        if self.cfg.wallet.signature_type == 0 and signer.lower() != self.wallet.lower():
            raise RuntimeError("WALLET_IDENTITY_MISMATCH: EOA funder differs from signer")
        if signer.lower() == self.wallet.lower() and self.cfg.wallet.signature_type != 0:
            raise RuntimeError("configured Safe/deposit funder equals signer EOA")
        if self.cfg.wallet.signature_type == 2:
            if not w3.eth.get_code(Web3.to_checksum_address(self.wallet)):
                raise RuntimeError("configured Safe is not deployed")
            safe = w3.eth.contract(address=Web3.to_checksum_address(self.wallet), abi=SAFE_ABI)
            owners = safe.functions.getOwners().call()
            threshold = safe.functions.getThreshold().call()
            if threshold != 1 or signer.lower() not in {a.lower() for a in owners}:
                raise RuntimeError("signer is not sole-threshold Safe owner")
        elif self.cfg.wallet.signature_type in (1, 3):
            # SecureClient.create can deploy an absent Deposit Wallet. A sweep
            # permits only MERGE/REDEEM mutations, so require deployed code first.
            if not w3.eth.get_code(Web3.to_checksum_address(self.wallet)):
                raise RuntimeError("configured deposit/proxy wallet is not deployed")
        elif self.cfg.wallet.signature_type not in (0, 1, 3):
            raise RuntimeError("unsupported wallet signature type")
        key = (BuilderApiKey(sec.builder_key, sec.builder_secret, sec.builder_passphrase)
               if sec.has_builder_creds else None)
        client = SecureClient.create(private_key=sec.pk, wallet=self.wallet, api_key=key)
        if str(client.signer).lower() != signer.lower():
            client.close()
            raise RuntimeError("WALLET_IDENTITY_MISMATCH: SDK signer differs from key")
        if str(client.wallet).lower() != self.wallet.lower():
            client.close()
            raise RuntimeError("SDK wallet differs from configured funder")
        expected_wallet_type = {0: "EOA", 1: "POLY_PROXY", 2: "GNOSIS_SAFE",
                                3: "DEPOSIT_WALLET"}[self.cfg.wallet.signature_type]
        if str(client.wallet_type) != expected_wallet_type:
            client.close()
            raise RuntimeError("SDK wallet type differs from configured architecture")
        self.w3 = w3
        self.client = client
        self.environment = get_environment_config(PRODUCTION)
        self.signer = signer
        self.wallet_type = expected_wallet_type
        self.auth_mode = "DIRECT" if expected_wallet_type == "EOA" else "BUILDER_RELAYED"

    def _balance(self, context: SettlementMarketContext, token: str) -> int:
        from web3 import Web3

        contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(context.position_erc1155_address),
            abi=BALANCE_ABI)
        return int(contract.functions.balanceOf(Web3.to_checksum_address(self.wallet), int(token)).call())

    def collateral_raw(self) -> int:
        from web3 import Web3

        contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(self.environment.collateral_token), abi=ERC20_ABI)
        return int(contract.functions.balanceOf(Web3.to_checksum_address(self.wallet)).call())

    def _descriptor(self, condition: str, indexed: list[Any]) -> Position:
        market_slugs = {row.slug for row in indexed if row.slug}
        if len(market_slugs) != 1:
            raise RuntimeError(f"ambiguous market slug for {condition}")
        market = self.client.get_market(slug=market_slugs.pop())
        if str(market.condition_id).lower() != condition.lower():
            raise RuntimeError(f"market condition mismatch for {condition}")
        context = resolve_settlement_context(market, self.environment)
        if any(row.negative_risk != context.negative_risk for row in indexed):
            raise RuntimeError(f"negative-risk metadata mismatch for {condition}")
        for row in indexed:
            validate_position_mapping(row, context)
        return Position(context, str(market.slug or condition), 0, 0, False, False,
                        market_closed=market.state.closed is True)

    def discover(self) -> list[Position]:
        rows = list(self.client.list_positions(user=self.wallet, status="OPEN",
                                               filter_type="TOKENS", filter_amount=0,
                                               page_size=100).iter_items())
        grouped: dict[str, list[Any]] = {}
        for row in rows:
            if str(row.wallet).lower() != self.wallet.lower():
                raise RuntimeError("position API returned a different wallet")
            if row.current_size > 0:
                grouped.setdefault(str(row.condition_id), []).append(row)
        self.indexed_positive_rows = sum(map(len, grouped.values()))
        positions = [self._read(self._descriptor(cid, indexed), indexed)
                     for cid, indexed in sorted(grouped.items())]
        return [position for position in positions if position.redeem_raw > 0]

    def _read(self, position: Position, indexed: list[Any] | None = None) -> Position:
        yes = self._balance(position.context, position.yes_token)
        no = self._balance(position.context, position.no_token)
        if yes < 0 or no < 0:
            raise RuntimeError("invalid negative on-chain balance")
        rows = indexed if indexed is not None else list(self.client.list_positions(
            user=self.wallet, condition_id=position.condition_id, status="OPEN",
            filter_type="TOKENS", filter_amount=0, page_size=100).iter_items())
        resolutions = self.client.get_resolutions(condition_ids=position.condition_id)
        matching = [r for r in resolutions if str(r.condition_id).lower() == position.condition_id.lower()]
        if len(matching) > 1:
            raise RuntimeError("conflicting resolution records")
        resolution = matching[0] if matching else None
        resolved = bool(resolution and resolution.status.value == "resolved" and
                        resolution.payouts is not None and position.market_closed)
        payouts = tuple(resolution.payouts) if resolved else None
        if payouts is not None and (len(payouts) != 2 or any(x < 0 or x > 1 for x in payouts)
                                    or sum(payouts) != 1):
            raise RuntimeError("invalid resolution payout vector")
        if position.protocol == "ctf":
            from web3 import Web3

            ctf = self.w3.eth.contract(
                address=Web3.to_checksum_address(self.environment.conditional_tokens),
                abi=PAYOUT_ABI)
            condition_bytes = bytes.fromhex(position.condition_id.removeprefix("0x"))
            denominator = int(ctf.functions.payoutDenominator(condition_bytes).call())
            if denominator:
                onchain = tuple(Decimal(int(ctf.functions.payoutNumerators(
                    condition_bytes, index).call())) / denominator for index in (0, 1))
                if payouts != onchain:
                    raise RuntimeError("on-chain resolution disagrees with Data API")
            elif resolved:
                raise RuntimeError("Data API resolved but CTF payout missing")
        for row in rows:
            if str(row.wallet).lower() != self.wallet.lower():
                raise RuntimeError("refreshed wallet identity mismatch")
            if row.negative_risk != position.context.negative_risk:
                raise RuntimeError("negative-risk metadata mismatch on refresh")
            validate_position_mapping(row, position.context)
        redeemable = bool(rows) and all(row.redeemable for row in rows)
        if redeemable and not resolved:
            raise RuntimeError("resolution disagreement")
        return replace(position, yes_raw=yes, no_raw=no, resolved=resolved,
                       redeemable=redeemable and resolved, payouts=payouts)

    def refresh(self, position: Position) -> Position:
        market = self.client.get_market(slug=position.market)
        if str(market.condition_id).lower() != position.condition_id.lower():
            raise RuntimeError("condition changed on refresh")
        context = resolve_settlement_context(market, self.environment)
        if context != position.context:
            raise RuntimeError("PROTOCOL_METADATA_CONFLICT: settlement context changed on refresh")
        return self._read(replace(position, market_closed=market.state.closed is True))

    def _assert_sdk_context(self, context: SettlementMarketContext, *, closed: bool | None = None) -> None:
        """Guard the SDK's independent dispatch-time market resolution (pinned 0.11.0)."""
        sdk = self.client._resolve_market_position_context(
            condition_id=context.condition_id, closed=closed)
        if (str(sdk.condition_id).lower() != context.condition_id.lower() or
                sdk.protocol != context.protocol or
                tuple(map(str, sdk.token_ids)) != (context.yes_id, context.no_id) or
                str(sdk.position_erc1155_address).lower() !=
                context.position_erc1155_address.lower() or
                (context.protocol == "ctf" and
                 (sdk.neg_risk != context.negative_risk or
                  str(sdk.adapter_address).lower() != str(context.adapter_address).lower()))):
            raise RuntimeError("PROTOCOL_METADATA_CONFLICT: SDK dispatch route differs")

    def submit_merge(self, position: Position, amount_raw: int) -> Any:
        self._assert_sdk_context(position.context)
        return self.client.merge_positions(condition_id=position.condition_id,
                                           amount=amount_raw, metadata="polymaker settlement merge")

    def submit_redeem(self, position: Position) -> Any:
        self._assert_sdk_context(position.context, closed=True)
        return self.client.redeem_positions(condition_id=position.condition_id,
                                            metadata="polymaker settlement redeem")

    def wait(self, handle: Any) -> tuple[str | None, str | None, bool]:
        from polymarket.errors import TransactionFailedError

        try:
            # SDK polling is bounded by count, but its default can take minutes.
            # Each HTTP request retains the SDK transport timeout. A timeout here
            # is an unknown terminal state, never proof of a failed submission.
            bounded = replace(handle, _max_polls=min(handle._max_polls, 24),
                              _poll_delay_s=min(handle._poll_delay_s, 5))
            result = bounded.wait()
        except TransactionFailedError:
            return (str(handle.transaction_id) if handle.transaction_id else None,
                    str(handle.transaction_hash) if handle.transaction_hash else None, False)
        return (str(result.transaction_id) if result.transaction_id else None,
                str(result.transaction_hash), True)

    def close(self) -> None:
        if self.client is not None:
            self.client.close()


def position_row(p: Position) -> dict[str, Any]:
    residual_index = 0 if p.yes_raw > p.no_raw else 1 if p.no_raw > p.yes_raw else None
    labels = (p.context.outcome0_label, p.context.outcome1_label)
    classes = []
    if selected_action(p) == "MERGE":
        classes.append("MERGEABLE")
    if selected_action(p) == "REDEEM":
        classes.append("REDEEMABLE")
    elif residual_index is not None:
        classes.append("UNRESOLVED_RESIDUAL" if not p.resolved else "NOT_REDEEMABLE")
    if not classes:
        classes.append("NOTHING_TO_DO")
    return {"condition_id": p.condition_id, "market": p.market,
            "yes_token": p.yes_token, "no_token": p.no_token,
            "yes_balance": units(p.yes_raw), "no_balance": units(p.no_raw),
            "outcomes": [
                {"index": 0, "label": labels[0], "asset_id": p.yes_token,
                 "balance": units(p.yes_raw)},
                {"index": 1, "label": labels[1], "asset_id": p.no_token,
                 "balance": units(p.no_raw)},
            ],
            "mergeable_qty": units(p.merge_raw),
            "selected_action": selected_action(p),
            "expected_redeem_proceeds": units(p.expected_redeem_raw()),
            "residual_outcome_index": residual_index,
            "residual_label": labels[residual_index] if residual_index is not None else None,
            "residual_balance": units(abs(p.yes_raw - p.no_raw)),
            "classification": classes,
            "protocol": p.protocol, "negative_risk": p.negative_risk,
            "position_erc1155_address": p.context.position_erc1155_address,
            "adapter_address": p.context.adapter_address,
            "resolved": p.resolved, "redeemable": p.redeemable,
            "market_closed": p.market_closed,
            "payouts": [str(x) for x in p.payouts] if p.payouts else None}


def _plan(positions: list[Position], collateral: int, wallet: str) -> str:
    merge = [p for p in positions if selected_action(p) == "MERGE"]
    redeem = [p for p in positions if selected_action(p) == "REDEEM"]
    lines = ["Settlement Sweep", f"Wallet: {wallet}",
             f"Starting collateral: ${units(collateral)}", f"MERGE candidates: {len(merge)}"]
    lines += [f"{p.market}: {p.context.outcome0_label} {units(p.yes_raw)} / "
              f"{p.context.outcome1_label} {units(p.no_raw)}; "
              f"merge {units(p.merge_raw)}; expected recovery ~${units(p.merge_raw)}"
              for p in merge]
    lines.append(f"REDEEM candidates: {len(redeem)}")
    lines += [f"{p.market}: redeem {units(p.redeem_raw)}; expected recovery "
              f"~${units(p.expected_redeem_raw())}" for p in redeem]
    return "\n".join(lines)


def _verify(backend: SweepBackend, before: Position, qty: int, collateral_before: int,
            action_type: str, expected: int, *, retries: int = 8, delay: float = 1.5) -> tuple[Position, int, bool]:
    post = before
    collateral = collateral_before
    for attempt in range(retries):
        post = backend.refresh(before)
        collateral = backend.collateral_raw()
        if action_type == "MERGE":
            balances_ok = (post.yes_raw == before.yes_raw - qty and
                           post.no_raw == before.no_raw - qty)
        else:
            balances_ok = post.yes_raw == 0 and post.no_raw == 0
        if balances_ok and collateral - collateral_before == expected:
            return post, collateral, True
        if attempt < retries - 1:
            time.sleep(delay)
    return post, collateral, False


def _with_heartbeat(label: str, emit: Any, operation: Any) -> Any:
    finished = threading.Event()

    def heartbeat() -> None:
        while not finished.wait(12):
            emit(label)

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        return operation()
    finally:
        finished.set()
        thread.join(timeout=1)


def _action(backend: SweepBackend, before: Position, action_type: str,
            *, on_prepared: Any = None, progress: Any = None) -> dict[str, Any]:
    emit = progress or (lambda _: None)
    emit(f"{action_type}_PREPARE {before.condition_id}")
    action = {"action_type": action_type, "market": before.market,
              "condition_id": before.condition_id, "qty": None,
              "pre_balances": position_row(before), "post_balances": None,
              "tx_id": None, "tx_hash": None, "submission_status": "NOT_SENT",
              "terminal_status": "NOT_SUBMITTED", "collateral_before": None,
              "collateral_after": None, "expected_recovery": None,
              "recovered_amount": None, "verification_status": "NOT_VERIFIED",
              "failure_phase": None, "exception_type": None,
              "safe_exception_message": None}
    try:
        fresh = _with_heartbeat(f"{action_type}_PREPARE {before.condition_id}", emit,
                                lambda: backend.refresh(before))
    except Exception as exc:
        action["failure_phase"] = "CONTEXT_REFRESH_FAILED"
        action["exception_type"] = type(exc).__name__
        return action
    try:
        if fresh.context != before.context or selected_action(fresh) != action_type:
            raise RuntimeError("identity or settlement state changed before transaction")
        qty = before.merge_raw if action_type == "MERGE" else before.redeem_raw
        if action_type == "MERGE":
            if fresh.merge_raw != qty or qty <= 0:
                raise RuntimeError("balance changed before merge submission")
            expected = qty
        else:
            if (fresh.redeem_raw != qty or qty <= 0 or
                    fresh.payouts != before.payouts):
                raise RuntimeError("redeem eligibility changed before submission")
            expected = fresh.expected_redeem_raw()
            if expected is None:
                raise RuntimeError("redeem payout unknown or not representable")
        collateral_before = backend.collateral_raw()
    except Exception as exc:
        action["failure_phase"] = "PRE_DISPATCH_VALIDATION_FAILED"
        action["exception_type"] = type(exc).__name__
        return action
    action.update(qty=units(qty), pre_balances=position_row(fresh),
                  collateral_before=units(collateral_before),
                  expected_recovery=units(expected))
    if on_prepared is not None:
        on_prepared(action)
    emit(f"{action_type}_SUBMITTING {fresh.condition_id}")
    try:
        handle = _with_heartbeat(
            f"{action_type}_SUBMITTING {fresh.condition_id}", emit,
            lambda: (backend.submit_merge(fresh, qty) if action_type == "MERGE"
                     else backend.submit_redeem(fresh)))
    except Exception as exc:
        from polymarket.errors import UserInputError

        if isinstance(exc, UserInputError):
            action["submission_status"] = "FAILED"
            action["terminal_status"] = "NOT_SUBMITTED"
            action["failure_phase"] = f"SDK_{action_type}_PRE_SUBMISSION_FAILED"
            action["exception_type"] = type(exc).__name__
            message = str(exc)
            # Preserve the SDK's market lookup reason while excluding arbitrary
            # request bodies, headers, or credential-bearing exception strings.
            prefix = f"No market found for condition {fresh.condition_id}"
            action["safe_exception_message"] = (
                prefix if message == prefix else "SDK rejected settlement input")
            return action
        action["submission_status"] = "SUBMISSION_UNKNOWN"
        action["terminal_status"] = "UNKNOWN"
        action["failure_phase"] = "SUBMISSION_UNKNOWN"
        action["exception_type"] = type(exc).__name__
        action["error"] = type(exc).__name__
        try:
            post, collateral_after, verified = _with_heartbeat(
                f"VERIFYING_POST_STATE {action_type} {fresh.condition_id}", emit,
                lambda: _verify(backend, fresh, qty, collateral_before,
                                action_type, expected))
            action["post_balances"] = position_row(post)
            action["collateral_after"] = units(collateral_after)
            action["recovered_amount"] = units(collateral_after - collateral_before)
            action["verification_status"] = (
                "POST_STATE_CHANGED_TX_UNKNOWN" if verified else "SUBMISSION_UNKNOWN")
        except Exception:
            pass  # still unknown; never retry submission
        return action
    action["submission_status"] = "SUBMITTED"
    emit(f"{action_type}_SUBMITTED {fresh.condition_id}")
    action["tx_id"] = str(getattr(handle, "transaction_id", None) or "") or None
    action["tx_hash"] = str(getattr(handle, "transaction_hash", None) or "") or None
    try:
        emit(f"WAITING_TERMINAL {action_type} {fresh.condition_id}")
        tx_id, tx_hash, confirmed = _with_heartbeat(
            f"WAITING_TERMINAL {action_type} {fresh.condition_id}", emit,
            lambda: backend.wait(handle))
    except Exception as exc:
        action["terminal_status"] = "UNKNOWN"
        action["failure_phase"] = "SUBMISSION_UNKNOWN"
        action["exception_type"] = type(exc).__name__
        action["error"] = type(exc).__name__
        return action
    action["tx_id"] = tx_id or action["tx_id"]
    action["tx_hash"] = tx_hash or action["tx_hash"]
    action["terminal_status"] = "CONFIRMED" if confirmed else "FAILED"
    if not confirmed:
        action["failure_phase"] = "TERMINAL_FAILED"
        emit(f"TERMINAL_FAILED {action_type} {fresh.condition_id}")
        return action
    emit(f"TERMINAL_SUCCESS {action_type} {fresh.condition_id}")
    emit(f"VERIFYING_POST_STATE {action_type} {fresh.condition_id}")
    try:
        post, collateral_after, verified = _with_heartbeat(
            f"VERIFYING_POST_STATE {action_type} {fresh.condition_id}", emit,
            lambda: _verify(backend, fresh, qty, collateral_before,
                            action_type, expected))
        action["post_balances"] = position_row(post)
        action["collateral_after"] = units(collateral_after)
        action["recovered_amount"] = units(collateral_after - collateral_before)
        action["verification_status"] = "VERIFIED" if verified else "INCOMPLETE_RECONCILIATION"
        if not verified:
            action["failure_phase"] = "POST_STATE_RECONCILIATION_FAILED"
        else:
            emit(f"VERIFIED {action_type} {fresh.condition_id}")
    except Exception as exc:
        action["verification_status"] = "INCOMPLETE_RECONCILIATION"
        action["failure_phase"] = "POST_STATE_RECONCILIATION_FAILED"
        action["exception_type"] = type(exc).__name__
        action["error"] = type(exc).__name__
    return action


def _report(row: dict[str, Any]) -> str:
    lines = ["# Settlement Sweep Report", "", "## Sweep",
             f"id: {row['sweep_id']}", f"wallet: {row['wallet']}",
             f"result: {row['status']}", "", "## Starting State",
             f"collateral: ${row['starting_collateral']}",
             f"positions: {len(row['positions_before'])}"]
    for kind in ("MERGE", "REDEEM"):
        lines += ["", f"## {kind}"]
        actions = [a for a in row["actions"] if a["action_type"] == kind]
        if not actions:
            lines.append("None.")
        for a in actions:
            lines += [f"{a['market']} ({a['condition_id']})", f"qty: {a['qty']}",
                      f"transaction: {a['terminal_status']} {a['tx_hash'] or a['tx_id'] or 'unknown'}",
                      f"collateral recovered: ${a['recovered_amount'] or 'unknown'}",
                      f"verification: {a['verification_status']}"]
            lines += [f"{outcome['label']} (index {outcome['index']}): {outcome['balance']}"
                      for outcome in a["pre_balances"]["outcomes"]]
    lines += ["", "## Ending State", f"collateral: ${row['ending_collateral'] or 'unknown'}",
              f"remaining positions: {len(row['positions_after'])}", "", "## Anomalies"]
    lines += row["anomalies"] or ["None."]
    lines += ["", "## Verdict", f"Settlement execution: {row['status']}",
              f"Capital recovered: ${row['total_collateral_recovered']}",
              "Capital recovered is not trade PnL."]
    return "\n".join(lines) + "\n"


def _unresolved_prior_conditions(base: Path, wallet: str) -> set[str]:
    """Prevent a new sweep from duplicating a prior uncertain dispatch."""
    unresolved: set[str] = set()
    for path in base.glob("*/pending.json"):
        try:
            pending = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if str(pending.get("wallet") or "").lower() == wallet.lower():
            unresolved.add(str(pending["condition_id"]))
    for path in base.glob("*/sweep.json"):
        try:
            prior = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if str(prior.get("wallet") or "").lower() != wallet.lower():
            continue
        for action in prior.get("actions", []):
            if (action.get("submission_status") == "SUBMISSION_UNKNOWN" or
                    action.get("terminal_status") == "UNKNOWN" or
                    action.get("verification_status") in
                    {"INCOMPLETE_RECONCILIATION", "POST_STATE_CHANGED_TX_UNKNOWN"}):
                unresolved.add(str(action["condition_id"]))
    return unresolved


def sweep(cfg: Config, backend: SweepBackend | None = None, *, print_plan: Any = print) -> dict[str, Any]:
    base = Path(cfg.paths.log_dir) / "settlement"
    base.mkdir(parents=True, exist_ok=True)
    sweep_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    directory = base / sweep_id
    directory.mkdir()
    pending_path = directory / "pending.json"
    row: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "sweep_id": sweep_id,
                           "wallet": cfg.secrets.browser_address or None, "started_at": now(),
                           "finished_at": None, "starting_collateral": None,
                           "ending_collateral": None, "positions_before": [], "actions": [],
                           "positions_after": [], "merge_recovered": units(0),
                           "redeem_recovered": units(0),
                           "total_collateral_recovered": units(0), "status": "ABORTED",
                           "anomalies": [], "static_gates": {}}
    client = backend or PolymarketSettlementBackend(cfg)
    lock_path = base / "sweep.lock"
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            row["status"] = "LOCKED"
            row["anomalies"].append("another settlement sweep owns the wallet lock")
        else:
            row["static_gates"]["concurrency_lock"] = "PASS"
            try:
                client.connect()
                if not client.wallet or client.wallet.lower() != (cfg.secrets.browser_address or "").lower():
                    raise RuntimeError("wallet identity mismatch")
                row["wallet"] = client.wallet
                row["static_gates"].update(wallet_identity="PASS", position_source="PASS",
                                           transaction_client="PASS", no_clob_reachable="PASS")
                start = client.collateral_raw()
                row["starting_collateral"] = units(start)
                row["static_gates"]["collateral_baseline"] = "PASS"
                positions = client.discover()
                row["positions_before"] = [position_row(p) for p in positions]
                row["static_gates"]["merge_protocol"] = "PASS"
                row["static_gates"]["redeem_protocol"] = "PASS"
                prior_unknown = _unresolved_prior_conditions(base, client.wallet)
                for position in positions:
                    if position.condition_id in prior_unknown and position.redeem_raw:
                        raise RuntimeError(f"unresolved prior submission for {position.condition_id}")
                print_plan("\n".join(f"{gate}: {value}" for gate, value in row["static_gates"].items()))
                print_plan(_plan(positions, start, client.wallet))
                def journal_pending(action: dict[str, Any]) -> None:
                    with pending_path.open("w") as stream:
                        json.dump({"wallet": client.wallet, "condition_id": action["condition_id"],
                                   "action_type": action["action_type"],
                                   "qty": action["qty"], "prepared_at": now()}, stream)
                        stream.flush()
                        os.fsync(stream.fileno())

                def clear_if_terminal(action: dict[str, Any]) -> None:
                    if (action["verification_status"] == "VERIFIED" or
                            action["terminal_status"] in {"FAILED", "NOT_SUBMITTED"}):
                        pending_path.unlink(missing_ok=True)

                stopped = False
                for kind in ("REDEEM", "MERGE"):
                    if stopped:
                        break
                    for position in positions:
                        if selected_action(position) != kind:
                            continue
                        qty = position.redeem_raw if kind == "REDEEM" else position.merge_raw
                        print_plan(f"Dispatch {kind}: {position.market} {position.condition_id} "
                                   f"qty={units(qty)} wallet={client.wallet} "
                                   f"protocol={position.protocol} neg_risk={position.negative_risk}")
                        action = _action(client, position, kind,
                                         on_prepared=journal_pending, progress=print_plan)
                        row["actions"].append(action)
                        clear_if_terminal(action)
                        if action["verification_status"] != "VERIFIED":
                            if action["terminal_status"] == "NOT_SUBMITTED":
                                row["status"] = f"{kind}_NOT_SUBMITTED"
                            elif (action["submission_status"] == "SUBMISSION_UNKNOWN" or
                                  action["terminal_status"] == "UNKNOWN"):
                                row["status"] = "SUBMISSION_UNKNOWN"
                            elif action["terminal_status"] == "FAILED":
                                row["status"] = "TERMINAL_FAILED"
                            else:
                                row["status"] = f"{kind}_INCOMPLETE_RECONCILIATION"
                            row["anomalies"].append(
                                f"{kind} {position.condition_id}: {action['failure_phase'] or action['verification_status']} "
                                f"{action['safe_exception_message'] or ''}".strip())
                            stopped = True
                            break
                    if not stopped:
                        positions = client.discover()
                row["positions_after"] = [position_row(p) for p in client.discover()]
                end = client.collateral_raw()
                row["ending_collateral"] = units(end)
                if not stopped:
                    row["status"] = "CONFIRMED" if row["actions"] else "NOTHING_TO_DO"
                for kind, key in (("MERGE", "merge_recovered"), ("REDEEM", "redeem_recovered")):
                    raw = sum(int(Decimal(a["recovered_amount"]) * SCALE)
                              for a in row["actions"] if a["action_type"] == kind and
                              a["verification_status"] == "VERIFIED")
                    row[key] = units(raw)
                recovered = (int(Decimal(row["merge_recovered"]) * SCALE) +
                             int(Decimal(row["redeem_recovered"]) * SCALE))
                row["total_collateral_recovered"] = units(recovered)
                if end - start != recovered and not stopped:
                    row["status"] = "INCOMPLETE_RECONCILIATION"
                    row["anomalies"].append("ending collateral differs from verified recovery")
            except Exception as exc:
                if row["status"] == "ABORTED":
                    row["status"] = "ABORTED" if not row["actions"] else "INCOMPLETE_RECONCILIATION"
                row["anomalies"].append(f"{type(exc).__name__}: {exc}")
            finally:
                client.close()
                fcntl.flock(lock, fcntl.LOCK_UN)
    row["finished_at"] = now()
    (directory / "sweep.json").write_text(json.dumps(row, indent=2) + "\n")
    (directory / "settlement_report.md").write_text(_report(row))
    (directory / "raw.log").write_text("\n".join(json.dumps(a) for a in row["actions"]) + "\n")
    row["artifact_dir"] = str(directory)
    return row


def preflight(cfg: Config, backend: SweepBackend | None = None,
              *, print_plan: Any = print) -> dict[str, Any]:
    """Read-only wallet and settlement eligibility check; no dispatch path exists here."""
    base = Path(cfg.paths.log_dir) / "settlement"
    base.mkdir(parents=True, exist_ok=True)
    sweep_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    directory = base / sweep_id
    directory.mkdir()
    row: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION, "sweep_id": sweep_id,
        "mode": "PREFLIGHT_ONLY", "mutation_count": 0,
        "wallet": cfg.secrets.browser_address or None,
        "signer": None, "wallet_type": None, "chain_id": cfg.wallet.chain_id,
        "auth_mode": None, "started_at": now(), "finished_at": None,
        "starting_collateral": None, "positions_before": [],
        "positive_position_rows": None, "validated_conditions": None,
        "merge_candidates": None, "redeem_candidates": None,
        "unresolved_residuals": None,
        "status": "PREFLIGHT_ABORTED", "gates": {}, "anomalies": [],
    }
    client = backend or PolymarketSettlementBackend(cfg)
    with (base / "sweep.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            row["status"] = "LOCKED"
            row["anomalies"].append("another settlement sweep owns the wallet lock")
        else:
            row["gates"]["concurrency_lock"] = "PASS"
            stage = "wallet_connection"
            try:
                client.connect()
                if not client.wallet or client.wallet.lower() != (cfg.secrets.browser_address or "").lower():
                    raise RuntimeError("WALLET_IDENTITY_MISMATCH")
                row["wallet"] = client.wallet
                row["signer"] = getattr(client, "signer", None)
                row["wallet_type"] = getattr(client, "wallet_type", None)
                row["auth_mode"] = getattr(client, "auth_mode", None)
                row["gates"].update(config_source="PASS", private_key="PASS",
                                    signer_derivation="PASS", funder_safe="PASS",
                                    signer_safe_relationship="PASS", sdk_wallet_identity="PASS",
                                    polygon_chain="PASS", builder_relayer_auth="PASS")
                stage = "collateral_read"
                collateral = client.collateral_raw()
                row["starting_collateral"] = units(collateral)
                row["gates"]["collateral_read"] = "PASS"
                stage = "position_discovery"
                positions = client.discover()
                row["positions_before"] = [position_row(p) for p in positions]
                row["positive_position_rows"] = getattr(client, "indexed_positive_rows", None)
                row["validated_conditions"] = len(positions)
                row["merge_candidates"] = sum(selected_action(p) == "MERGE" for p in positions)
                row["redeem_candidates"] = sum(selected_action(p) == "REDEEM" for p in positions)
                row["unresolved_residuals"] = [
                    {"condition_id": item["condition_id"],
                     "outcome_index": item["residual_outcome_index"],
                     "label": item["residual_label"], "balance": item["residual_balance"]}
                    for item in row["positions_before"]
                    if item["residual_outcome_index"] is not None and not item["resolved"]]
                row["gates"]["position_discovery"] = "PASS"
                row["status"] = "PREFLIGHT_PASS"
            except Exception as exc:
                row["gates"][stage] = "FAIL"
                row["positive_position_rows"] = getattr(client, "indexed_positive_rows", None)
                # SDK/RPC exceptions may carry signed request details; record only
                # controlled gate codes or the exception class.
                message = str(exc)
                if isinstance(exc, RuntimeError) and (
                    message.startswith("PROTOCOL_METADATA_CONFLICT:") or message in {
                        "POLYGON_CHAIN_MISMATCH", "CANONICAL_PRIVATE_KEY_MISSING",
                        "CANONICAL_FUNDER_MISSING", "BUILDER_AUTH_MISSING",
                        "WALLET_IDENTITY_MISMATCH"}
                ):
                    row["anomalies"].append(message)
                else:
                    status = getattr(getattr(exc, "response", None), "status_code", None)
                    row["anomalies"].append(
                        f"{type(exc).__name__}: HTTP {status}" if status else type(exc).__name__)
            finally:
                client.close()
                fcntl.flock(lock, fcntl.LOCK_UN)
    row["finished_at"] = now()
    report = ["# Settlement Preflight", "", f"id: {sweep_id}",
              f"result: {row['status']}", f"wallet: {row['wallet'] or 'unknown'}",
              f"signer: {row['signer'] or 'unknown'}",
              f"wallet type: {row['wallet_type'] or 'unknown'}",
              f"auth mode: {row['auth_mode'] or 'unknown'}", "",
              f"collateral: ${row['starting_collateral'] or 'unknown'}",
              f"positive position rows: {row['positive_position_rows'] if row['positive_position_rows'] is not None else 'unknown'}",
              f"validated conditions: {row['validated_conditions'] if row['validated_conditions'] is not None else 'unknown'}",
              f"MERGE candidates: {row['merge_candidates'] if row['merge_candidates'] is not None else 'unknown'}",
              f"REDEEM candidates: {row['redeem_candidates'] if row['redeem_candidates'] is not None else 'unknown'}",
              "mutation count: 0", "", "## Gates"]
    report.extend(f"{key}: {value}" for key, value in row["gates"].items())
    report.extend(["", "## Positions"])
    for position in row["positions_before"]:
        report.extend([f"{position['market']} ({position['condition_id']})",
                       f"protocol: {position['protocol']} | negative-risk: {position['negative_risk']}",
                       f"resolved: {position['resolved']} | redeemable: {position['redeemable']}"])
        report.extend(
            f"outcome {outcome['index']}: {outcome['label']} | asset {outcome['asset_id']} | balance {outcome['balance']}"
            for outcome in position["outcomes"])
        report.append(f"classification: {', '.join(position['classification'])}")
        report.append(f"selected action: {position['selected_action'] or 'NONE'}")
        if position["selected_action"] == "REDEEM":
            report.append(f"expected proceeds: ${position['expected_redeem_proceeds']}")
        if position["residual_outcome_index"] is not None:
            report.append(f"residual: {position['residual_label']} {position['residual_balance']}")
    if not row["positions_before"]:
        report.append("None validated.")
    report.extend(["", "## Anomalies", *(row["anomalies"] or ["None."])])
    (directory / "sweep.json").write_text(json.dumps(row, indent=2) + "\n")
    (directory / "settlement_report.md").write_text("\n".join(report) + "\n")
    row["artifact_dir"] = str(directory)
    print_plan("Settlement Preflight")
    for gate, result in row["gates"].items():
        print_plan(f"{gate}: {result}")
    print_plan(f"Wallet: {str(row['wallet'])[:10]}… | Signer: {str(row['signer'])[:10]}…")
    print_plan(f"Wallet type: {row['wallet_type']} | Auth: {row['auth_mode']}")
    print_plan(f"Collateral: ${row['starting_collateral'] or 'unknown'} | "
               f"Positions: {len(row['positions_before'])} | "
               f"MERGE: {row['merge_candidates'] if row['merge_candidates'] is not None else 'unknown'} | "
               f"REDEEM: {row['redeem_candidates'] if row['redeem_candidates'] is not None else 'unknown'}")
    for position in row["positions_before"]:
        print_plan(f"{position['market']} {position['condition_id']}: "
                   f"{position['selected_action'] or 'NONE'} | "
                   f"{position['outcomes'][0]['label']}={position['yes_balance']} "
                   f"{position['outcomes'][1]['label']}={position['no_balance']}")
    print_plan(f"No mutation: CONFIRMED | Result: {row['status']}")
    for anomaly in row["anomalies"]:
        print_plan(f"Anomaly: {anomaly}")
    return row
