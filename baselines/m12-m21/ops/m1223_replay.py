"""Full offline M12 development replay, using all 24 frozen source markets."""

import argparse
import asyncio
import hashlib
import json
import resource
import shutil
import sqlite3
import sys
import time
from copy import deepcopy
from pathlib import Path
from uuid import uuid4


def file_hash(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def validate_dataset(dataset):
    strategy = dataset.get("strategy", "M12")
    rows = dataset.get("markets", [])
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        raise ValueError("FROZEN_24_MARKET_DEVELOPMENT_DATASET_REQUIRED")
    names = [r.get("market") for r in rows]
    if (
        strategy not in {"M12", "M13", "M14", "M15", "M16", "M17", "M18", "M19"}
        or dataset.get("schema") != strategy.lower() + "-predeclared-development-dataset-v1"
        or dataset.get("sample_role") != "DEVELOPMENT_ONLY"
        or dataset.get("denominator") != 24
        or len(rows) != 24
        or len(set(names)) != 24
        or any(not isinstance(m, str) or not m.startswith("btc-updown-5m-") for m in names)
    ):
        raise ValueError("FROZEN_24_MARKET_DEVELOPMENT_DATASET_REQUIRED")
    epochs = [int(m.rsplit("-", 1)[1]) for m in names]
    if epochs != list(range(epochs[0], epochs[0] + 24 * 300, 300)):
        raise ValueError("DATASET_ORDER_OR_CONTIGUITY_CHANGED")
    if any(r.get("source_validity") != "VALID" for r in rows):
        raise ValueError("SOURCE_MARKET_UNVERIFIED")
    return rows


def verify_source(record):
    source = Path(record["source_input"])
    if not source.is_file() or file_hash(source) != record["source_input_sha256"]:
        raise ValueError("SOURCE_INPUT_MISSING_OR_CHANGED:" + record["market"])
    if file_hash(source.parent / "simulation.sqlite3") != record["source_database_sha256"]:
        raise ValueError("SOURCE_AUDIT_CHANGED:" + record["market"])
    return source


def canonical_signals(database):
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        return {
            tuple(json.loads(raw))
            for (raw,) in db.execute("""SELECT json_array(
            json_extract(payload,'$.payload.token'), json_extract(payload,'$.payload.anchor_price'),
            input_seq, json_extract(payload,'$.subindex')) FROM audit
            WHERE kind='SOP_EVIDENCE' AND json_extract(payload,'$.event')='SIGNAL_CONFIRMED'""")
        }


def replay_initial_event(event, parameters, provenance, parameter_revision=None):
    """Bind native INIT to the selected parameters, retaining the source label."""
    from m1223_sim import definition

    selected = definition(parameters)
    initial = deepcopy(event)
    cfg = initial["definition"]["settings"]
    original_sop = cfg.get("sop", {})
    if original_sop.get("strategy") != selected["strategy"]:
        reuse = provenance.get("public_input_reuse", {})
        approved = {"M12_VALIDITY_OVERLAP", "M23_FIRST_SELL_WHILE_VALID", "M21_ENDPOINT_COFLOW", "M7_PAUSE_RELEASE", "M9_EX_VALIDITY_OVERLAP"}
        expected = {
            "mode": "PUBLIC_INPUT_ONLY_NEW_STRATEGY_LEDGER",
            "source_strategy": original_sop.get("strategy"),
            "source_sop_sha256": hashlib.sha256(
                json.dumps(original_sop, sort_keys=True).encode()).hexdigest(),
            "target_strategy": selected["strategy"],
            "target_variant": selected.get("variant_id"),
        }
        if (selected.get("variant_id") not in approved or reuse != expected
                or cfg.get("profiles") != {} or not original_sop.get("real_orders") == 0):
            raise ValueError("REPLAY_CANNOT_MIX_STRATEGY_SOURCE_DATA")
    source_revision = deepcopy(cfg.get("sop_parameter_revision"))
    if parameter_revision is None:
        if original_sop.get("parameter_sha256") != selected["parameter_sha256"]:
            raise ValueError("CHANGED_REPLAY_PARAMETERS_REQUIRE_EXPLICIT_REVISION")
        parameter_revision = source_revision
    if parameter_revision is not None:
        number = parameter_revision.get("revision_round")
        if (
            parameter_revision.get("strategy") != selected["strategy"]
            or parameter_revision.get("parameter_sha256") != selected["parameter_sha256"]
            or parameter_revision.get("parameters") != selected["parameters"]
            or parameter_revision.get("pnl_used_for_selection") is not False
            or type(number) is not int or not 0 <= number <= 3
            or (number and not str(parameter_revision.get("hypothesis", "")).strip())
        ):
            raise ValueError("REPLAY_NATIVE_PARAMETER_REVISION_DIFFERS_FROM_SELECTED_VALUES")
        cfg["sop_parameter_revision"] = deepcopy(parameter_revision)
    cfg.update(
        sop=selected,
        sop_replay={**provenance, "scope": "PROVENANCE_ONLY_NOT_SIGNAL_INPUT",
                    "source_sop_parameter_revision": source_revision,
                    "source_sop_definition": original_sop},
    )
    return initial


async def replay_market(record, output, parameters, owner, provenance, parameter_revision=None):
    import m511_sample_quality
    from m1223_sim import SopEngine
    from m1223_sim_forward import execution_configuration

    from polymaker.m1234_sim.events import read_events, write_json

    source = verify_source(record)
    gate = record.get("recorded_eligibility") or {
        "status": "UNAVAILABLE",
        "reason": "ORIGINAL_ELIGIBILITY_NOT_RECORDED",
    }
    original_gate = m511_sample_quality.own_market_gate
    m511_sample_quality.own_market_gate = lambda _project, _market: gate
    engine = SopEngine(output, parameters=parameters)
    engine.store.run_owner = owner
    try:
        for event in read_events(source):
            if event["kind"] == "INIT":
                event = replay_initial_event(event, parameters, provenance, parameter_revision)
                cfg = event["definition"]["settings"]
                cfg["sop_execution"] = execution_configuration()
            await engine.ingest(event)
        if not engine.ended or engine.seq != record["snapshot_input_seq"]:
            raise ValueError("REPLAY_DID_NOT_REACH_SEALED_SOURCE_WATERMARK")
        engine.persist()
        result = {
            "market": record["market"],
            "input_seq": engine.seq,
            "validity": engine.validity["status"],
            "complete_window": engine.coverage["complete_window"],
        }
        write_json(
            output / "replay-source.json",
            {
                **record,
                **provenance,
                "input_copied": False,
                "requires_retained_original_input": True,
            },
        )
        await engine.close()
        engine.retire_runtime()
        engine.brokers.clear()
        engine.mark_targets.clear()
        engine.marks.clear()
        engine.last_books = engine.tick_verification = None
        return result
    except BaseException as exc:
        if not engine.store.closed:
            await engine.close()
        write_json(
            output / "replay-failure.json",
            {
                "status": "FAIL",
                "input_seq": engine.seq,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "source": record,
            },
        )
        raise
    finally:
        m511_sample_quality.own_market_gate = original_gate


async def replay(dataset, parameter_document, output, provenance):
    from m1223_report import market_result, summary
    from m1223_sim import definition
    from sop_signals.metrics import coverage
    from sop_signals.parameters import load_revision

    from polymaker.m1234_sim.events import write_json

    records = validate_dataset(dataset)
    parameters, revision = load_revision(parameter_document)
    if revision["strategy"] != dataset.get("strategy", "M12"):
        raise ValueError("REPLAY_DATASET_PARAMETER_STRATEGY_MISMATCH")
    if output.exists():
        raise ValueError("NEW_REPLAY_IDENTITY_AND_OUTPUT_REQUIRED")
    output.mkdir(parents=True)
    owner = {"run_id": str(uuid4()), "mode": "OFFLINE_DEVELOPMENT_REPLAY", **provenance}
    write_json(
        output / "replay-plan.json",
        {
            "owner": owner,
            "revision": revision,
            "dataset": dataset,
            "is_realtime_validation": False,
            "maximum_output_mib": 2048,
            "minimum_free_disk_mib": 4096,
            "max_rss_mib": 512,
        },
    )
    markets, orders, comparisons, samples = [], [], [], []
    total_bytes = 0
    try:
        if shutil.disk_usage(output).free < 6 * 1024**3:
            raise ValueError("REPLAY_DISK_PREFLIGHT_REQUIRES_2_GIB_PLUS_4_GIB_RESERVE")
        for record in records:
            await replay_market(record, output / record["market"], parameters, owner, provenance,
                                parameter_revision=revision)
            gate = record.get("recorded_eligibility") or {
                "status": "UNAVAILABLE",
                "reason": "ORIGINAL_ELIGIBILITY_NOT_RECORDED",
            }
            m, o = market_result(
                output / record["market"] / "simulation.sqlite3",
                Path("/home/ubuntu/polymaker"),
                eligibility=gate,
            )
            old_m, old_o = market_result(
                Path(record["source"]) / "simulation.sqlite3",
                Path("/home/ubuntu/polymaker"),
                eligibility=gate,
            )
            markets.append(m)
            orders.extend(o)
            before = canonical_signals(Path(record["source"]) / "simulation.sqlite3")
            after = canonical_signals(output / record["market"] / "simulation.sqlite3")
            comparisons.append(
                {
                    "market": record["market"],
                    "source_signal_count": old_m["signal_count"],
                    "replayed_signal_count": m["signal_count"],
                    "source_submissions": len(old_o),
                    "replayed_submissions": len(o),
                    "added_signal_publication_keys": sorted(after - before),
                    "removed_signal_publication_keys": sorted(before - after),
                }
            )
            total_bytes += sum(
                p.stat().st_size for p in (output / record["market"]).iterdir() if p.is_file()
            )
            rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
            free = shutil.disk_usage(output).free
            samples.append(
                {
                    "finished_markets": len(markets),
                    "rss_peak_mib": rss,
                    "output_bytes": total_bytes,
                    "free_bytes": free,
                    "wall_epoch": time.time(),
                }
            )
            write_json(
                output / "progress.json",
                {
                    "status": "RUNNING",
                    "completed_markets": len(markets),
                    "declared_denominator": 24,
                    "revision": revision,
                    "last_resource": samples[-1],
                },
            )
            if rss > 512 or total_bytes >= 2 * 1024**3 or free < 4 * 1024**3:
                raise RuntimeError("REPLAY_RESOURCE_CAPACITY_EXHAUSTED")
        counts = coverage([r["market"] for r in records], markets)
        primary = [r for r in orders if r["primary_eligible"]]
        result = {
            "status": "PASS",
            "mode": "OFFLINE_DEVELOPMENT_REPLAY",
            "is_realtime_validation": False,
            "admission_granted": False,
            "owner": owner,
            "revision": revision,
            "frequency": counts,
            "source_comparisons": comparisons,
            "markets": markets,
            "all_recorded": {
                mode: summary(orders, counts["all_recorded_signal_count"], mode)
                for mode in ("assumed", "simulated")
            },
            "primary": {
                mode: summary(
                    primary, sum(m["signal_count"] for m in markets if m["primary_eligible"]), mode
                )
                for mode in ("assumed", "simulated")
            },
            "resources": samples,
            "limitations": [
                "Proved intervals only; unknowns retained",
                "Development frequency is not independent validation or profitability proof",
            ],
        }
        write_json(output / "replay-result.json", result)
        # The parameter chooser consumes this frequency-only artifact, not PnL.
        write_json(
            output / "frequency-only.json",
            {
                "status": "PASS",
                "owner": owner,
                "revision": revision,
                "sop_definition": definition(parameters),
                "market_epochs": [int(r["market"].rsplit("-", 1)[1]) for r in records],
                "frequency": counts,
                "admission_granted": False,
                "selection_uses_pnl": False,
            },
        )
        import csv

        with (output / "orders.csv").open("w", newline="") as f:
            writer = csv.DictWriter(
                f, fieldnames=list(orders[0]) if orders else ["market", "order_id"]
            )
            writer.writeheader()
            writer.writerows(orders)
        write_json(
            output / "progress.json",
            {
                "status": "PASS",
                "completed_markets": len(markets),
                "declared_denominator": 24,
                "revision": revision,
                "last_resource": samples[-1],
                "terminal_result": str(output / "replay-result.json"),
            },
        )
        return result
    except BaseException as exc:
        write_json(
            output / "replay-result.json",
            {
                "status": "FAIL",
                "owner": owner,
                "revision": revision,
                "completed_markets": len(markets),
                "declared_denominator": 24,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "resources": samples,
            },
        )
        write_json(
            output / "progress.json",
            {
                "status": "FAIL",
                "completed_markets": len(markets),
                "declared_denominator": 24,
                "revision": revision,
                "error_type": type(exc).__name__,
                "terminal_result": str(output / "replay-result.json"),
            },
        )
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--parameters", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    runtime = args.runtime.resolve()
    for rel, expected in json.loads((runtime / "frozen-manifest.json").read_text()).items():
        if file_hash(runtime / rel) != expected:
            raise ValueError("REPLAY_FROZEN_SOURCE_CHANGED:" + rel)
    sys.path.insert(0, str(runtime / "src"))
    sys.path.insert(0, str(runtime / "extension"))
    dataset = json.loads(args.dataset.read_text())
    document = json.loads(args.parameters.read_text())
    output = args.output.resolve()
    if Path("/home/ubuntu/polymaker/research_outputs/m1223_sim") not in output.parents:
        raise ValueError("REPLAY_OUTPUT_MUST_BE_IN_OWN_TASK_DIRECTORY")
    result = asyncio.run(
        replay(
            dataset,
            document,
            output,
            {
                "dataset_sha256": file_hash(args.dataset),
                "parameters_sha256": file_hash(args.parameters),
                "runtime": str(runtime),
                "replay_wrapper_sha256": file_hash(Path(__file__)),
            },
        )
    )
    print(
        json.dumps(
            {"status": result["status"], "frequency": result["frequency"], "output": str(output)},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
