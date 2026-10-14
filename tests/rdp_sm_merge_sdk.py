"""Fake external settlement SDK/RPC only; keep the SM and settlement backend real."""

from decimal import Decimal
from types import SimpleNamespace

from eth_abi import encode
from eth_account import Account
from eth_utils import keccak
from web3.providers.base import BaseProvider


def install(monkeypatch, venue, cfg):
    from polymarket import SecureClient
    from polymarket._internal.actions.relayer.positions import normalize_market_position_context
    from polymarket._internal.environment import get_environment_config
    from polymarket.environments import PRODUCTION
    from polymarket.transactions import SyncGaslessTransactionHandle
    from web3 import Web3

    cfg.secrets.builder_key = "dummy-builder-key"
    cfg.secrets.builder_secret = "dummy-builder-secret"
    cfg.secrets.builder_passphrase = "dummy-builder-passphrase"
    monkeypatch.setenv("POLY_BUILDER_KEY", "dummy-builder-key")
    monkeypatch.setenv("POLY_BUILDER_SECRET", "dummy-builder-secret")
    monkeypatch.setenv("POLY_BUILDER_PASSPHRASE", "dummy-builder-passphrase")
    venue.merge_submissions = []
    venue.merge_rpc_calls = []
    venue.merge_fault = None
    venue.chain_merged = Decimal(0)
    env = get_environment_config(PRODUCTION)
    tx_hash = "0x" + "12" * 32
    signer = Account.from_key(cfg.secrets.pk).address

    def balances(token):
        qty = sum((Decimal(leg["matched_amount"]) for trade in venue.trades
                   if trade["status"] == "CONFIRMED" for leg in trade["maker_orders"]
                   if leg["asset_id"] == str(token)), Decimal(0))
        return int((qty - venue.chain_merged) * 1_000_000)

    class Provider(BaseProvider):
        def make_request(self, method, params):
            venue.merge_rpc_calls.append((method, venue.time))
            if method == "eth_chainId":
                result = "0x89"
            elif method == "eth_getCode":
                result = "0x01"
            elif method == "eth_call":
                data = bytes.fromhex(params[0]["data"][2:])
                if data[:4] == keccak(text="getOwners()")[:4]:
                    owner = signer if venue.merge_fault != "owner" else "0x" + "33" * 20
                    result = "0x" + encode(["address[]"], [[owner]]).hex()
                elif data[:4] == keccak(text="getThreshold()")[:4]:
                    result = "0x" + encode(["uint256"], [1]).hex()
                elif data[:4] == keccak(text="balanceOf(address,uint256)")[:4]:
                    result = "0x" + encode(["uint256"], [balances(int.from_bytes(data[-32:]))]).hex()
                elif data[:4] == keccak(text="balanceOf(address)")[:4]:
                    result = "0x" + encode(["uint256"], [int(venue.capital["balance"])]).hex()
                else:
                    raise AssertionError(f"Unexpected offline RPC selector {data[:4].hex()}")
            elif method == "eth_getTransactionReceipt":
                result = {"transactionHash": tx_hash, "blockHash": "0x" + "34" * 32,
                          "blockNumber": "0x1", "transactionIndex": "0x0", "logs": [],
                          "cumulativeGasUsed": "0x1", "gasUsed": "0x1",
                          "status": "0x0" if venue.merge_fault == "failed" else "0x1"}
            else:
                raise AssertionError(f"Unexpected offline RPC method {method}")
            return {"jsonrpc": "2.0", "id": 1, "result": result}

    class Relayer:
        def __init__(self, qty):
            self.qty = qty
            self.applied = False

        def get_json(self, path):
            assert path == "/v1/account/transactions/offline-merge"
            if venue.merge_fault == "poll_timeout":
                raise TimeoutError("offline merge status unknown")
            if not self.applied and venue.merge_fault != "failed":
                self.applied = True
                venue.chain_merged += Decimal(self.qty) / 1_000_000
                if venue.merge_fault != "cash":
                    venue.capital["balance"] = str(int(venue.capital["balance"]) + self.qty)
                venue.positions = [{"token_id": token, "current_size": balances(token) / 1_000_000,
                                    "avg_price": .5} for token in ("1", "2") if balances(token)]
            return {"transaction_id": "offline-merge", "transaction_hash": tx_hash,
                    "state": "STATE_FAILED" if venue.merge_fault == "failed" else "STATE_CONFIRMED"}

    class Client:
        def __init__(self):
            self.signer = signer
            self.wallet = cfg.secrets.browser_address
            self.wallet_type = {0: "EOA", 2: "GNOSIS_SAFE"}[cfg.wallet.signature_type]

        def get_market(self, *, slug):
            assert slug == venue.raw["slug"]
            yes = "3" if venue.merge_fault == "token" else "1"
            return SimpleNamespace(id=1, condition_id=venue.raw["conditionId"], slug=slug,
                version=SimpleNamespace(value="v1"), state=SimpleNamespace(neg_risk=False),
                outcomes=SimpleNamespace(yes=SimpleNamespace(token_id=yes, position_id=None, label="Up"),
                                         no=SimpleNamespace(token_id="2", position_id=None, label="Down")))

        def _resolve_market_position_context(self, *, condition_id, closed=None):
            assert condition_id == venue.raw["conditionId"]
            if venue.merge_fault == "closed_lookup" and closed is None:
                from polymarket.errors import UserInputError

                raise UserInputError(f"No market found for condition {condition_id}")
            return normalize_market_position_context(self.get_market(slug=venue.raw["slug"]),
                context="offline SDK boundary", collateral_adapter=env.collateral_adapter,
                neg_risk_collateral_adapter=env.neg_risk_collateral_adapter,
                conditional_tokens=env.conditional_tokens, neg_risk_adapter=env.neg_risk_adapter,
                position_manager=env.position_manager)

        def merge_positions(self, *, condition_id, amount, metadata):
            self._resolve_market_position_context(condition_id=condition_id)
            assert not any(row["status"] == "LIVE" for row in venue.rows.values())
            venue.merge_submissions.append((venue.time, condition_id, amount, self.wallet))
            if venue.merge_fault == "submit_timeout":
                raise TimeoutError("offline uncertain submission")
            return SyncGaslessTransactionHandle("offline-merge", tx_hash, Relayer(amount), 20, 0)

        def close(self):
            pass

    monkeypatch.setattr(Web3, "HTTPProvider", lambda *_args, **_kwargs: Provider())
    monkeypatch.setattr(SecureClient, "create", classmethod(lambda cls, **kwargs: Client()))
