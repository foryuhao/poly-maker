import asyncio
import json
from collections import Counter
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from m1223_public_protocol import BaselinePublicAPI, PublicMarketError, validate_resolution
from m1223_shared72 import SharedCaptureFeed, SharedRun


def gamma_market(epoch, version):
    market = {"slug": f"btc-updown-5m-{epoch}", "version": version,
              "conditionId": f"0x{epoch:064x}", "outcomes": '["Down","Up"]',
              "clobTokenIds": '["102","101"]', "active": True, "closed": False,
              "acceptingOrders": True,
              "endDate": datetime.fromtimestamp(epoch + 300, UTC).isoformat()}
    if version == "v2":
        market["positionIds"] = ["202", "201"]
    return market


@pytest.mark.asyncio
async def test_adjacent_protocols_share_ids_across_tick_retries_websocket_and_settlement(tmp_path):
    gamma_calls, tick_calls = Counter(), Counter()

    def request(req):
        if req.url.path.endswith("/tick-size"):
            asset = req.url.params["token_id"]
            tick_calls[asset] += 1
            if asset == "201" and tick_calls[asset] == 1:
                return httpx.Response(200, json={"minimum_tick_size": "unavailable"})
            return httpx.Response(200, json={"minimum_tick_size": "0.01"})
        epoch = int(req.url.path.rsplit("-", 1)[1])
        gamma_calls[epoch] += 1
        if epoch != 600 or gamma_calls[epoch] == 2:
            return httpx.Response(200, json=gamma_market(epoch, "v1"))
        market = gamma_market(epoch, "v2")
        if gamma_calls[epoch] == 1:
            del market["positionIds"]
        return httpx.Response(200, json=market)

    api = BaselinePublicAPI(transport=httpx.MockTransport(request))
    run = SharedRun(tmp_path, tmp_path, {"max_markets": 3}, {}, api=api)
    run.server_now = lambda: 0
    try:
        for epoch, version, ids in ((300, "v1", ["101", "102"]), (600, "v2", ["201", "202"]),
                                    (900, "v1", ["101", "102"])):
            raw = gamma_market(epoch, version)
            meta, ticks = await run.prepare(raw["slug"], epoch)
            assert meta.protocol_version == version and list(ticks) == ids
            feed = SharedCaptureFeed(meta, asyncio.Queue())
            feed.receive = AsyncMock()
            feed.heartbeat = AsyncMock()
            ws = SimpleNamespace(send=AsyncMock())
            await feed.observe(ws)
            ws.send.assert_awaited_once_with(json.dumps({"assets_ids": ids, "type": "market"}))

            identity = {"slug": meta.slug, "condition_id": meta.condition_id, "protocol_version": version,
                        "token_ids": {"YES": meta.yes.token_id, "NO": meta.no.token_id}}
            raw.update(closed=True, outcomePrices='["0","1"]')
            active, resolved = (("umaResolutionStatus", "resolutionStatus") if version == "v2"
                                else ("resolutionStatus", "umaResolutionStatus"))
            raw[active] = "resolved"
            assert validate_resolution(raw, identity) is None
            raw[resolved] = "resolved"
            proof = validate_resolution(raw, identity)
            assert proof["winner"] == "YES" and proof["token_ids"] == identity["token_ids"]
            with pytest.raises(PublicMarketError, match="VERSION_MISMATCH"):
                validate_resolution({**raw, "version": "v1" if version == "v2" else "v2"}, identity)
    finally:
        await api.close()
    assert gamma_calls == {300: 1, 600: 3, 900: 1}
    assert tick_calls == {"101": 2, "102": 2, "201": 2, "202": 1}
    assert "PUBLIC_MARKET_VERSION_MISMATCH" in (tmp_path / "runtime.jsonl").read_text()


@pytest.mark.asyncio
async def test_unusable_market_does_not_stop_following_market(tmp_path):
    now = 295

    def request(req):
        nonlocal now
        if req.url.path.endswith("/tick-size"):
            return httpx.Response(200, json={"minimum_tick_size": "0.01"})
        epoch = int(req.url.path.rsplit("-", 1)[1])
        raw = gamma_market(epoch, "v2" if epoch == 300 else "v1")
        if epoch == 300:
            raw.pop("positionIds")
            now = 315  # exhaust the existing T15 metadata window
        return httpx.Response(200, json=raw)

    api = BaselinePublicAPI(transport=httpx.MockTransport(request))
    run = SharedRun(tmp_path, tmp_path, {"max_markets": 2}, {}, api=api)
    run.server_now = lambda: now
    try:
        await run.market("btc-updown-5m-300", 300)
        assert not run.stop.is_set()
        now = 595
        meta, ticks = await run.prepare("btc-updown-5m-600", 600)
        assert meta.protocol_version == "v1" and list(ticks) == ["101", "102"]
    finally:
        await api.close()
    events = [json.loads(row) for row in (tmp_path / "runtime.jsonl").read_text().splitlines()]
    assert [(row["market"], row["event"]) for row in events] == [
        ("btc-updown-5m-300", "MARKET_PREPARE_RETRY"),
        ("btc-updown-5m-300", "MARKET_MISSING"),
        ("btc-updown-5m-600", "MARKET_PROTOCOL_SELECTED"),
    ]
