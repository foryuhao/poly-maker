"""Market protocol identity for CTF and Polymarket Protocol V2."""

import re
from dataclasses import dataclass, field
from typing import Literal

from polymaker.domain import MarketMeta, TokenMeta

EXCHANGE_V3_POLYGON = "0xe3333700cA9d93003F00f0F71f8515005F6c00Aa"


@dataclass(frozen=True, slots=True)
class V2MarketMeta(MarketMeta):
    # Keep V1's serialized market shape, including existing ledger snapshots.
    protocol_version: Literal["v2"] = field(default="v2", init=False)


def market_protocol(market):
    version = (market.get("protocol_version", "v1") if isinstance(market, dict)
               else getattr(market, "protocol_version", "v1"))
    if version not in ("v1", "v2"):
        raise ValueError("MARKET_PROTOCOL_UNSUPPORTED")
    return version


def gamma_protocol(raw):
    if "version" not in raw and raw.get("positionIds") is None:
        # Historical CTF payloads predate the version field.
        return "v1"
    version = raw.get("version")
    if version not in ("v1", "v2"):
        raise ValueError("GAMMA_MARKET_PROTOCOL_UNSUPPORTED")
    return version


def position_ids(raw):
    ids = raw.get("positionIds")
    if (not isinstance(ids, list) or len(ids) != 2
            or any(not isinstance(asset, str) or re.fullmatch(r"[1-9][0-9]{0,77}", asset) is None
                   or int(asset) >= 2**256 for asset in ids)
            or ids[0] == ids[1]):
        raise ValueError("GAMMA_V2_POSITION_IDS_INVALID")
    return ids


def market_from_snapshot(raw):
    cls = V2MarketMeta if market_protocol(raw) == "v2" else MarketMeta
    data = dict(raw)
    data.pop("protocol_version", None)
    data["tokens"] = tuple(TokenMeta(**t) for t in raw["tokens"])
    return cls(**data)


def exchange_v3(chain_id):
    if chain_id != 137:
        raise ValueError("EXCHANGE_V3_CHAIN_UNSUPPORTED")
    return EXCHANGE_V3_POLYGON
