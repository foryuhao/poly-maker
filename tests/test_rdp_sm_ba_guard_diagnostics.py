"""Real SDK submit boundaries stay live; only duplicate diagnostics are skipped."""

from dataclasses import asdict
from decimal import Decimal as D

import pytest

import polymaker.rdp_sm_lifecycle as lifecycle_module
from test_rdp_sm_ba import absorption as normal_absorption
from test_rdp_sm_ba import execution_setup
from test_rdp_sm_m2_execution import journal_records
from test_rdp_sm_obi_execution import cleanup
from test_rdp_sm_repair import env as env
from test_rdp_sm_repair_ba_tolerance import absorption, book, tolerant_setup


def observe(monkeypatch, lifecycle, mutate=None):
    native_guard = lifecycle.guard
    native_format = lifecycle.ba_entry_evidence
    native_admission = lifecycle_module.entry_admission
    checks, formatted, admissions = [], [], []

    def admission(*args, **kwargs):
        result = native_admission(*args, **kwargs)
        admissions.append(result)
        return result

    def evidence(*args, **kwargs):
        result = native_format(*args, **kwargs)
        formatted.append((args, kwargs, result))
        return result

    def guard(*args, **kwargs):
        index = len(checks)+1
        if mutate:
            mutate(index)
        allowed = native_guard(*args, **kwargs)
        checks.append({"allowed": allowed, "reason": lifecycle.guard_reason,
                       "evidence": lifecycle.ba_admission})
        return allowed

    monkeypatch.setattr(lifecycle_module, "entry_admission", admission)
    monkeypatch.setattr(lifecycle, "ba_entry_evidence", evidence)
    monkeypatch.setattr(lifecycle, "guard", guard)
    return checks, formatted, admissions


def final_decision(e, kind):
    return next(r["data"]["payload"]["intent"] for r in journal_records(e.journal.path)
                if r["kind"] == kind)


async def test_three_real_repair_guards_recompute_admission_but_format_only_final_evidence(env, monkeypatch):
    setup, controller = await tolerant_setup(env, monkeypatch)
    venue, e, feed, lifecycle = setup[:4]
    try:
        absorption(feed, "2")
        slot = await lifecycle.prepare(controller, *e.economic_inventory(), "diagnostics-success")
        assert slot is not None
        checks, formatted, admissions = observe(monkeypatch, lifecycle)
        result, _ = await lifecycle.activate(controller, slot)
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert len(checks) == len(admissions) == 3
        assert all(c["allowed"] for c in checks)
        assert len(formatted) == 1
        assert all(isinstance(c["evidence"], tuple) for c in checks[:2])
        assert all(c["evidence"][1] is admission
                   for c, admission in zip(checks[:2], admissions[:2], strict=True))
        assert checks[-1]["evidence"] == formatted[0][2]
        assert all(a is not b for a, b in zip(admissions, admissions[1:], strict=False))
        assert all(a.observed_monotonic <= b.observed_monotonic
                   for a, b in zip(admissions, admissions[1:], strict=False))
        assert len(venue.post_requests) == 2
        assert venue.post_requests[-1][0]["postOnly"]
        assert result.requested_quotes == list(slot.candidates[0].quotes)
        assert result.fill_deadline_monotonic == admissions[-1].deadline
        await e.journal.flush()
        decision = final_decision(e, "sm_opposite_ba_final_decision")
        evidence = decision["ba_admission"]
        for key, value in asdict(admissions[-1]).items():
            assert evidence[key] == value
        assert set(evidence["books"]) == {"YES", "NO"}
        assert evidence["snapshot_id"] == decision["final_snapshot"]["snapshot_id"]
        assert evidence["generation"] == slot.snapshot.generation
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("stage", [1, 2, 3])
@pytest.mark.parametrize("fault,reason", [
    ("quote", "FINAL_QUOTES_OR_DIRECTION_CHANGED"),
    ("entry_age", "BA_ENTRY_STALE"),
    ("cancel_rtt", "BA_CANCEL_LATENCY_HIGH"),
])
async def test_each_native_boundary_still_blocks_changes_and_preserves_failure_evidence(
    env, monkeypatch, stage, fault, reason
):
    setup, controller = await tolerant_setup(env, monkeypatch)
    venue, e, feed, lifecycle = setup[:4]
    try:
        absorption(feed, "2")
        slot = await lifecycle.prepare(controller, *e.economic_inventory(), "diagnostics-failure")
        assert slot is not None

        def mutate(index):
            if index != stage:
                return
            if fault == "quote":
                ask = feed.books["2"].best_ask + feed.books["2"].tick
                setup[-2]["no_ask"] = str(ask)
                feed.apply(book(feed, feed.books["2"].best_bid, ask))
            elif fault == "entry_age":
                controller.ba_config.max_entry_age_ms = .01
            else:
                e.reads.network.observe("DELETE", .751, batch_id="prior-close")

        checks, formatted, admissions = observe(monkeypatch, lifecycle, mutate)
        result, _ = await lifecycle.activate(controller, slot)
        assert len(checks) == len(admissions) == stage
        assert all(c["allowed"] for c in checks[:-1])
        assert checks[-1]["allowed"] is False and checks[-1]["reason"] == reason
        assert len(formatted) == 1
        if stage < 3:
            assert formatted[0][1]["admission"] is admissions[-1]
            assert formatted[0][1]["wall"] is not None
        assert not any(a["post_started"] for a in result.placement_results)
        assert len(venue.post_requests) == 1
        assert not controller.episodes.selected or not controller.episodes.selected.consumed
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        await e.journal.flush()
        decision = final_decision(e, "sm_opposite_ba_final_decision")
        assert decision["aborted_reason"] == reason
        assert decision["post_started"] is None
        evidence = decision["ba_admission"]
        for key, value in asdict(admissions[-1]).items():
            assert evidence[key] == value
        assert set(evidence["books"]) == {"YES", "NO"}
        assert evidence["network_allowed"] is True
    finally:
        await cleanup(setup)


async def test_early_failure_does_not_reuse_previous_success_evidence(env, monkeypatch):
    setup, controller = await tolerant_setup(env, monkeypatch)
    venue, e, feed, lifecycle = setup[:4]
    try:
        absorption(feed, "2")
        slot = await lifecycle.prepare(controller, *e.economic_inventory(), "diagnostics-early-failure")
        assert slot is not None

        def mutate(index):
            if index == 2:
                e.cooldown.deadline = e.monotonic()+1

        checks, formatted, admissions = observe(monkeypatch, lifecycle, mutate)
        result, _ = await lifecycle.activate(controller, slot)
        assert len(checks) == 2 and len(admissions) == 1
        assert not formatted
        assert checks[-1]["reason"] == "COOLDOWN_ACTIVE"
        assert checks[-1]["evidence"] is None
        assert not any(a["post_started"] for a in result.placement_results)
        assert len(venue.post_requests) == 1
        await e.journal.flush()
        decision = final_decision(e, "sm_opposite_ba_final_decision")
        assert decision["ba_admission"] is None
    finally:
        await cleanup(setup)


async def test_normal_ba_keeps_all_existing_full_diagnostics(env, monkeypatch):
    setup = await execution_setup(env, monkeypatch)
    venue, e, feed, lifecycle, normal = setup[:5]
    try:
        normal_absorption(feed)
        slot = await lifecycle.prepare(normal, D(0), D(0), "normal-diagnostics-unchanged")
        assert slot is not None
        checks, formatted, admissions = observe(monkeypatch, lifecycle)
        result, _ = await lifecycle.activate(normal, slot)
        assert result.close_barrier == "SAFE_TO_CONTINUE", result.block_reason
        assert len(checks) == len(formatted) == len(admissions) == 3
        assert all(c["allowed"] and c["evidence"] for c in checks)
        assert len(venue.post_requests) == 1
    finally:
        await cleanup(setup)


@pytest.mark.parametrize("stage", [1, 2])
async def test_sdk_failure_between_guards_keeps_last_checked_evidence(env, monkeypatch, stage):
    setup, controller = await tolerant_setup(env, monkeypatch)
    venue, e, feed, lifecycle = setup[:4]
    client = e.gateway._client
    try:
        absorption(feed, "2")
        slot = await lifecycle.prepare(controller, *e.economic_inventory(), "sdk-diagnostics-failure")
        assert slot is not None
        checks, formatted, admissions = observe(monkeypatch, lifecycle)
        if stage == 1:
            def fail_before_serialization(*args, **kwargs):
                raise ValueError("TEST_SDK_SERIALIZATION_FAILURE")

            monkeypatch.setattr(client, "post_orders", fail_before_serialization)
        else:
            native_value = client._operation_value

            def changed_expected_hash(name):
                return "invalid-body-hash" if name == "expected_body_hash" else native_value(name)

            monkeypatch.setattr(client, "_operation_value", changed_expected_hash)
        result, _ = await lifecycle.activate(controller, slot)
        assert len(checks) == len(admissions) == stage
        assert all(c["allowed"] for c in checks)
        assert len(formatted) == 1
        assert formatted[0][1]["admission"] is admissions[-1]
        assert not any(a["post_started"] for a in result.placement_results)
        assert len(venue.post_requests) == 1
        await e.journal.flush()
        decision = final_decision(e, "sm_opposite_ba_final_decision")
        assert decision["post_started"] is None
        assert decision["post_error"] == ("ValueError" if stage == 1 else "_Issue")
        assert decision["health"] is None
        for key, value in asdict(admissions[-1]).items():
            assert decision["ba_admission"][key] == value
        assert decision["ba_admission"]["reason"] is None
    finally:
        await cleanup(setup)
