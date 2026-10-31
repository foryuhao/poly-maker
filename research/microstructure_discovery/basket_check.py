"""Screen a complete partition for simultaneous gross executable basket discrepancies."""

import json
import sys
from pathlib import Path

from execution_study import vwap
from study import Book, array


def analyze(root):
    root = Path(root)
    es = json.loads((root / "event_rules.json").read_text())
    active = []
    closed = []
    for e in es:
        for m in e["markets"]:
            outs = array(m["outcomes"])
            tokens = array(m["clobTokenIds"])
            yes = tokens[outs.index("Yes")]
            if m.get("closed"):
                closed.append(
                    dict(
                        question=m["question"], prices=array(m.get("outcomePrices", [])), asset=yes
                    )
                )
            else:
                active.append(dict(asset=yes, question=m["question"]))
    rows = []
    for lineno, line in enumerate((root / "context.jsonl").open(), 1):
        r = json.loads(line)
        if r.get("url") != "https://clob.polymarket.com/books" or not isinstance(
            r.get("data"), list
        ):
            continue
        by_asset = {b["asset_id"]: b for b in r["data"]}
        entries = []
        for m in active:
            raw = by_asset.get(m["asset"])
            if raw is None:
                break
            b = Book()
            b.update(dict(raw, event_type="book"))
            buy = vwap(b, "BUY")
            sell = vwap(b, "SELL")
            if buy is None or sell is None:
                break
            entries.append(
                dict(
                    asset=m["asset"], buy_vwap5=buy, sell_vwap5=sell, timestamp=raw.get("timestamp")
                )
            )
        if len(entries) != len(active):
            continue
        ask = sum(e["buy_vwap5"] for e in entries)
        bid = sum(e["sell_vwap5"] for e in entries)
        rows.append(
            dict(
                received=r["received"],
                request_duration=r["received"] - r["requested"],
                context_line=lineno,
                sum_ask=ask,
                sum_bid=bid,
                gross_buy_surplus=1 - ask,
                gross_sell_surplus=bid - 1,
                legs=entries,
            )
        )
    result = dict(
        active_bins=active,
        closed_bins=closed,
        snapshots=len(rows),
        positive_gross_buy_snapshots=sum(r["gross_buy_surplus"] > 1e-9 for r in rows),
        positive_gross_sell_snapshots=sum(r["gross_sell_surplus"] > 1e-9 for r in rows),
        best_gross_buy_surplus=max((r["gross_buy_surplus"] for r in rows), default=None),
        best_gross_sell_surplus=max((r["gross_sell_surplus"] for r in rows), default=None),
        rows=rows,
        qualification="Screen only: requires closed-bin zero payoff verification, exhaustive partition rules, fee/latency/leg-risk/collateral validation. Endpoint books are not atomic execution and no fills are assumed. Negative results do not imply efficiency at sub-30s horizons.",
    )
    (root / "basket_screen.json").write_text(json.dumps(result, indent=2))
    print(
        json.dumps(
            {k: v for k, v in result.items() if k not in ("rows", "active_bins", "closed_bins")},
            indent=2,
        )
    )


if __name__ == "__main__":
    analyze(sys.argv[1])
