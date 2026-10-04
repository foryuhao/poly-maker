"""Fetch official Polymarket settlement for the frozen Backtest 01 universe.

Read-only HTTP: Gamma market by slug and CLOB market by condition ID.
Run: python3 research_tools/dp_backtest_01b_settlement_labels.py
"""
from __future__ import annotations

import csv
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "research_data/m4a"
OUT = ROOT / "research_outputs/dp_backtest_01"
AUDIT = OUT / "dataset_audit.csv"
LABELS = OUT / "settlement_labels.csv"
SOURCES = OUT / "settlement_sources.json"
GAMMA = "https://gamma-api.polymarket.com/markets/slug/"
CLOB = "https://clob.polymarket.com/markets/"


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "poly-maker-dp-research/1.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def as_array(value):
    return json.loads(value) if isinstance(value, str) else value


def main():
    rows = list(csv.DictReader(AUDIT.open()))
    selected = [r for r in rows if r["execution_replay_usable"] == "True"]
    if len(selected) != 20 or len({r["market"] for r in selected}) != 20:
        raise RuntimeError("Frozen Backtest 01 universe is not exactly 20 unique markets")
    manifests = {}
    for path in DATA.glob("*/manifest.json"):
        item = json.loads(path.read_text())
        manifests[item["session_id"]] = item
    output = []
    sources = {}
    for row in selected:
        m = manifests[row["session"]]
        slug = row["market"]
        condition = m["condition_id"]
        gamma_url = GAMMA + slug
        clob_url = CLOB + condition
        record = dict(market=slug, slug=slug, replay_session_id=row["session"],
                      market_start_us=int(slug.rsplit("-", 1)[1]) * 1_000_000,
                      market_end_us=(int(slug.rsplit("-", 1)[1]) + 300) * 1_000_000,
                      condition_id=condition, yes_token_id=m["yes_token_id"],
                      no_token_id=m["no_token_id"], resolved=False,
                      winning_outcome="UNRESOLVED", winning_token_id="",
                      clob_closed="", gamma_closed="", gamma_market_id="",
                      gamma_outcomes="", gamma_outcome_prices="",
                      official_resolution_fields="", official_source=f"{clob_url} | {gamma_url}",
                      retrieved_at="", identity_validation="UNVERIFIED", notes="")
        try:
            gamma = fetch(gamma_url)
            clob = fetch(clob_url)
            record["retrieved_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            sources[slug] = {"gamma": gamma, "clob": clob, "retrieved_at": record["retrieved_at"]}
            record["clob_closed"] = clob.get("closed")
            record["gamma_closed"] = gamma.get("closed")
            record["gamma_market_id"] = gamma.get("id") or ""
            labels = as_array(gamma.get("outcomes"))
            prices = as_array(gamma.get("outcomePrices"))
            gamma_tokens = as_array(gamma.get("clobTokenIds"))
            record["gamma_outcomes"] = json.dumps(labels)
            record["gamma_outcome_prices"] = json.dumps(prices)
            tokens = clob.get("tokens") or []
            expected = {m["yes_token_id"], m["no_token_id"]}
            start_s = int(slug.rsplit("-", 1)[1])
            gamma_end = gamma.get("endDate") or ""
            end_ok = bool(gamma_end) and int(datetime.fromisoformat(gamma_end.replace("Z", "+00:00")).timestamp()) == start_s + 300
            same = (gamma.get("slug") == slug and clob.get("market_slug") == slug
                    and str(gamma.get("conditionId", "")).lower() == condition.lower()
                    and str(clob.get("condition_id", "")).lower() == condition.lower()
                    and isinstance(gamma_tokens, list) and set(map(str, gamma_tokens)) == expected
                    and isinstance(tokens, list) and {str(t.get("token_id")) for t in tokens} == expected
                    and end_ok)
            record["identity_validation"] = "MATCH" if same else "MISMATCH"
            winners = [t for t in tokens if t.get("winner") is True]
            gamma_price_match = (
                isinstance(prices, list) and isinstance(gamma_tokens, list)
                and len(prices) == len(gamma_tokens) == 2 and len(winners) == 1
                and all(float(price) == (1.0 if str(tok) == str(winners[0].get("token_id")) else 0.0)
                        for tok, price in zip(gamma_tokens, prices))
            )
            if same and clob.get("closed") is True and gamma.get("closed") is True and len(winners) == 1 and gamma_price_match:
                winner_id = str(winners[0]["token_id"])
                record["winning_token_id"] = winner_id
                record["winning_outcome"] = "YES" if winner_id == m["yes_token_id"] else "NO"
                record["resolved"] = True
                record["official_resolution_fields"] = "CLOB tokens[].winner=true; both closed=true; Gamma outcomePrices=1/0 corroboration"
            else:
                record["notes"] = ("identity, closed status, unique CLOB token winner, or Gamma 1/0 "
                                   "cross-check failed; no winner assigned")
        except Exception as exc:
            record["retrieved_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            record["notes"] = f"official HTTP retrieval/parse failed: {type(exc).__name__}: {exc}"
        output.append(record)
        print(slug, record["winning_outcome"], record["identity_validation"], record["notes"])
    if len(output) != 20:
        raise RuntimeError("Settlement label row count changed")
    with LABELS.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(output[0]))
        writer.writeheader()
        writer.writerows(output)
    SOURCES.write_text(json.dumps(sources, indent=2) + "\n")
    print("resolved", sum(r["resolved"] for r in output), "/", len(output))


if __name__ == "__main__":
    main()
