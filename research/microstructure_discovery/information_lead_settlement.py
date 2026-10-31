"""Read-only terminal accounting for V8; observed quotes are never own executions."""

import argparse
import asyncio
import hashlib
import json
import time
from decimal import Decimal
from pathlib import Path

import httpx

CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
RPC = "https://polygon-bor-rpc.publicnode.com"
KBASE = "https://api.elections.kalshi.com/trade-api/v2"
SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def terminal_value(decision, result, evidence):
    """Keep errors/pending exposures in the denominator and charge cap costs."""
    spec = decision["plan"]
    cost = Decimal(str(spec["poly_budget"])) + Decimal(str(spec["kalshi_cost_bound"]))
    reserve = Decimal(str(spec["reserve"]))
    worst = -cost - reserve
    base = dict(id=decision["id"], event=decision["event"], status="PENDING_OBSERVATION")
    if result is None:
        return dict(base, net_lower_bound=float(worst), terminal=False)
    if result["status"] != "PAIRED_PENDING_SETTLEMENT":
        bound = result.get("net_lower_bound")
        return dict(
            base,
            status=result["status"],
            net_lower_bound=float(worst if bound is None else Decimal(str(bound))),
            terminal=bound is not None,
        )
    try:
        chain = evidence["chain"]
        assert chain["chain_id"] == 137 and chain["contract"].lower() == CTF.lower()
        assert chain["condition"].lower() == decision["market"].lower()
        denominator = int(chain["denominator"])
        numerators = [int(x) for x in chain["numerators"]]
        assert denominator > 0 and len(numerators) == 2
        assert all(0 <= n <= denominator for n in numerators)
        assert sum(numerators) == denominator
        native = evidence["resolution"]["data"]
        exact = [r for r in native if r["condition_id"].lower() == decision["market"].lower()]
        assert len(exact) == 1 and exact[0]["status"] == "resolved"
        assert exact[0]["payouts"] == [n * 1000000 // denominator for n in numerators]
        pair = decision["pair"]
        if decision["asset"] == pair["poly_yes"]:
            index = 0
        elif decision["asset"] == pair["poly_no"]:
            index = 1
        else:
            raise ValueError("asset absent from registered mapping")
        market = evidence["kalshi"]["market"]
        assert market["ticker"] == decision["ticker"]
        assert market["status"] in ("finalized", "settled")
        assert market["result"] in ("yes", "no"), "nonbinary exception requires separate audit"
        kpay = Decimal(10 if market["result"] == spec["kalshi_side"] else 0)
        ppay = Decimal(str(result["token_floor"])) * Decimal(numerators[index]) / denominator
        net = ppay + kpay - cost - reserve
        return dict(
            base,
            status="PAIRED_PAYOUT_VERIFIED",
            terminal=True,
            poly_payout_floor=float(ppay),
            kalshi_payout=float(kpay),
            cost_cap=float(cost),
            net_lower_bound=float(net),
            complementary_payouts=(numerators[index] / denominator + float(kpay) / 10 == 1),
        )
    except (AssertionError, KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        return dict(
            base,
            status="PAIRED_FINALITY_UNCONFIRMED",
            terminal=False,
            net_lower_bound=float(worst),
            reason=repr(exc),
        )


async def collect_evidence(client, decision):
    async def get(url, params=None):
        r = await client.get(url, params=params)
        r.raise_for_status()
        return r.json()

    async def rpc(method, params):
        r = await client.post(RPC, json=dict(jsonrpc="2.0", id=1, method=method, params=params))
        r.raise_for_status()
        obj = r.json()
        if "error" in obj:
            raise RuntimeError(obj["error"])
        return obj["result"]

    async def chain():
        chain_id = int(await rpc("eth_chainId", []), 16)
        block = await rpc("eth_blockNumber", [])
        cid = decision["market"].removeprefix("0x")
        if len(cid) != 64:
            raise ValueError("condition must be bytes32")

        async def call(data):
            return int(await rpc("eth_call", [dict(to=CTF, data=data), block]), 16)

        values = await asyncio.gather(
            call("0xdd34de67" + cid),
            *(call("0x0504c814" + cid + format(i, "064x")) for i in range(2)),
        )
        return dict(
            source=RPC,
            chain_id=chain_id,
            block=block,
            condition=decision["market"],
            contract=CTF,
            denominator=values[0],
            numerators=values[1:],
        )

    requested = time.time()
    results = await asyncio.gather(
        chain(),
        get("https://data-api.polymarket.com/v2/resolutions", {"condition": decision["market"]}),
        get(KBASE + "/markets/" + decision["ticker"]),
        return_exceptions=True,
    )
    row = dict(requested=requested, received=time.time())
    for key, value in zip(["chain", "resolution", "kalshi"], results, strict=True):
        row[key] = dict(error=repr(value)) if isinstance(value, BaseException) else value
    return row


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("root", type=Path)
    a = p.parse_args()
    registration = json.loads((a.root / "registration.json").read_text())
    decisions = [json.loads(s) for s in (a.root / "decisions.jsonl").read_text().splitlines() if s]
    results_file = a.root / "results.jsonl"
    results = (
        [json.loads(s) for s in results_file.read_text().splitlines() if s]
        if results_file.exists()
        else []
    )
    indexed = {r["id"]: r for r in results}
    assert len(indexed) == len(results), "duplicate result rows require audit"
    assert len({d["id"] for d in decisions}) == len(decisions), "duplicate decisions"
    assert set(indexed) <= {d["id"] for d in decisions}, "orphan results"
    rows, sources = [], []
    async with httpx.AsyncClient(timeout=15) as client:
        for decision in decisions:
            assert decision["decision_at"] >= registration["created"]
            result = indexed.get(decision["id"])
            evidence = {}
            if result and result["status"] == "PAIRED_PENDING_SETTLEMENT":
                evidence = await collect_evidence(client, decision)
                folder = a.root / decision["id"]
                target = folder / f"settlement_sources_{time.time_ns()}.json"
                target.write_text(json.dumps(evidence, indent=2))
                sources.append(
                    dict(path=str(target), sha256=hashlib.sha256(target.read_bytes()).hexdigest())
                )
            rows.append(terminal_value(decision, result, evidence))
    summary = dict(
        checked_at=time.time(),
        registration=registration,
        decisions=len(decisions),
        independent_game_count=len({d["event"] for d in decisions}),
        terminal_observations=sum(r["terminal"] for r in rows),
        conservative_net_sum=sum(r["net_lower_bound"] for r in rows),
        rows=rows,
        evidence_files=sources,
        qualification="Conditional public-book paper bounds; no own orders, fills, or proven strategy expected return. Zero decisions supply no edge evidence. Pending exposures charged full committed cap costs.",
        code_sha256=SOURCE_SHA256,
    )
    (a.root / "settlement_accounting.json").write_text(json.dumps(summary, indent=2))
    print(
        json.dumps(
            {
                k: summary[k]
                for k in [
                    "decisions",
                    "independent_game_count",
                    "terminal_observations",
                    "conservative_net_sum",
                ]
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
