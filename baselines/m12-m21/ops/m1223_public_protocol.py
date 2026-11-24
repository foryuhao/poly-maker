"""Pin the official public market protocol for the shared simulation baseline."""

import json
import re
import time
from dataclasses import dataclass, fields
from decimal import Decimal, InvalidOperation

from polymaker.dashboard.settlement import BASE
from polymaker.dashboard.settlement import validate_resolution as legacy_resolution
from polymaker.domain import MarketMeta
from polymaker.maker_public import VirtualPublicAPI, public_market


class PublicMarketError(ValueError):
    """Unusable public evidence for one market, which can be re-read."""

    def __init__(self, message, *, protocol_version=None):
        super().__init__(message)
        self.protocol_version = protocol_version


@dataclass(frozen=True, slots=True, kw_only=True)
class BaselineMarket(MarketMeta):
    protocol_version: str


def json_array(raw, key):
    value = raw.get(key)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise PublicMarketError(f"PUBLIC_{key}_INVALID") from exc
    if not isinstance(value, list) or len(value) != 2:
        raise PublicMarketError(f"PUBLIC_{key}_INVALID")
    return value


def assets(raw, version):
    if version not in ("v1", "v2"):
        raise PublicMarketError("PUBLIC_MARKET_VERSION_UNSUPPORTED")
    if raw.get("version") != version:
        raise PublicMarketError("PUBLIC_MARKET_VERSION_MISMATCH")
    field = "positionIds" if version == "v2" else "clobTokenIds"
    ids = raw.get(field) if version == "v2" else json_array(raw, field)
    if (not isinstance(ids, list) or len(ids) != 2
            or any(not isinstance(asset, str) or re.fullmatch(r"[1-9][0-9]{0,77}", asset) is None
                   or int(asset) >= 2**256 for asset in ids)
            or ids[0] == ids[1]):
        raise PublicMarketError(f"PUBLIC_{field}_INVALID")
    outcomes = json_array(raw, "outcomes")
    if any(not isinstance(outcome, str) for outcome in outcomes):
        raise PublicMarketError("PUBLIC_MARKET_OUTCOMES_INVALID")
    mapping = dict(zip((outcome.casefold() for outcome in outcomes), ids, strict=True))
    if set(mapping) != {"up", "down"}:
        raise PublicMarketError("PUBLIC_MARKET_OUTCOMES_INVALID")
    return ids, {"YES": mapping["up"], "NO": mapping["down"]}


class BaselinePublicAPI(VirtualPublicAPI):
    async def market(self, slug):
        try:
            raw = await self.raw_market(slug)
        except ValueError as exc:
            raise PublicMarketError("PUBLIC_GAMMA_RESPONSE_INVALID") from exc
        version = raw.get("version")
        if version not in ("v1", "v2"):
            raise PublicMarketError("PUBLIC_MARKET_VERSION_UNSUPPORTED")
        try:
            ids, _ = assets(raw, version)
            # Supply the version-selected IDs to the existing BTC metadata parser.
            # The Gamma response itself is never rewritten in the evidence ledger.
            meta = public_market({**raw, "clobTokenIds": ids}, slug)
        except PublicMarketError as exc:
            raise PublicMarketError(str(exc), protocol_version=version) from exc
        except (ValueError, TypeError, AttributeError) as exc:
            raise PublicMarketError(f"PUBLIC_MARKET_METADATA_INVALID:{exc}", protocol_version=version) from exc
        return BaselineMarket(
            **{field.name: getattr(meta, field.name) for field in fields(MarketMeta)},
            protocol_version=version,
        )

    async def ticks(self, tokens):
        try:
            return await super().ticks(tokens)
        except (ValueError, KeyError, TypeError, InvalidOperation) as exc:
            raise PublicMarketError("PUBLIC_TICK_RESPONSE_INVALID") from exc

    async def resolution(self, identity):
        raw = await self.raw_market(identity["slug"], purpose="settlement")
        return validate_resolution(raw, identity)


def validate_resolution(raw, identity):
    version = identity.get("protocol_version")
    if version is None:
        # Old recorded definitions contain no protocol field. Replay them under
        # the same settlement rules they were captured with.
        return legacy_resolution(raw, identity)
    if (raw.get("slug") != identity["slug"]
            or not identity.get("condition_id")
            or str(raw.get("conditionId", "")).lower() != identity["condition_id"].lower()):
        raise PublicMarketError("SETTLEMENT_MARKET_IDENTITY_MISMATCH")
    ids, mapping = assets(raw, version)
    if mapping != identity["token_ids"]:
        raise PublicMarketError("SETTLEMENT_ASSET_IDENTITY_MISMATCH")
    status_field = "resolutionStatus" if version == "v2" else "umaResolutionStatus"
    status = raw.get(status_field)
    if raw.get("closed") is not True or status != "resolved":
        return None
    try:
        prices = [Decimal(str(value)) for value in json_array(raw, "outcomePrices")]
    except InvalidOperation as exc:
        raise PublicMarketError("SETTLEMENT_PAYOUT_NOT_BINARY_FINAL") from exc
    if not all(price.is_finite() for price in prices) or sorted(prices) != [0, 1]:
        raise PublicMarketError("SETTLEMENT_PAYOUT_NOT_BINARY_FINAL")
    winning_id = ids[prices.index(Decimal(1))]
    return {"slug": identity["slug"], "condition_id": identity["condition_id"],
            "token_ids": mapping, "winner": next(side for side, asset in mapping.items() if asset == winning_id),
            "source": BASE + identity["slug"], "protocol_version": version,
            "resolution_status": status, "closed": True, "outcome_prices": [str(price) for price in prices],
            ("position_ids" if version == "v2" else "clob_token_ids"): ids, "observed_at": time.time()}
