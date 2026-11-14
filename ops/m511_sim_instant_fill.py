"""Research strategy assumed-fill comparison, using the verified SUBMIT ledger."""

import hashlib
import importlib.util
from pathlib import Path

from m511_sample_quality import read_gate
from m511_sim import GROUPS, M5_M10_GROUPS, M5_M10_REVISION, M5_M10_THROUGH_REVISION, selected_groups

spec = importlib.util.spec_from_file_location(
    "m511_assumed_ledger_implementation", Path(__file__).with_name("m234_sim_main_instant_fill.py")
)
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)

base.MODEL = "m511-instant-fill-t240-v1:" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
base.STRATEGIES = GROUPS
base.SCENARIOS = ("research/trades-only", "research/cancel-half", "research/trade-through")
base.QUERY = (
    "SELECT seq,input_seq,kind,payload FROM audit WHERE seq>? AND seq<=? AND "
    "(kind IN (" + ",".join("?" for _ in base.KINDS) + ") OR (kind='SIGNAL' AND "
    "json_extract(payload,'$.strategy') IN (" + ",".join("'" + s + "'" for s in GROUPS) + ") AND "
    "json_extract(payload,'$.event')='CANDIDATE')) ORDER BY seq"
)


class ResearchInstantFill(base.InstantFill):
    report_heading = "M5–M10 / M5–M11 发单即成交：信号验证（范围以策略行及运行定义为准）"
    execution_column = "执行模式"
    execution_description = "假设成交与模拟成交独立；仅为毛收益。行情覆盖不足的未决订单可能已有已知部分成交。"

    def scenarios_for(self, plan):
        settings = plan.get("settings", {}).get("m5_m11", {})
        return (("research/trade-through",) if settings.get("revision") == M5_M10_THROUGH_REVISION
                else ("research/trades-only", "research/cancel-half"))

    def strategies_for(self, plan):
        settings = plan.get("settings", {}).get("m5_m11", {})
        if settings.get("revision") in (M5_M10_REVISION, M5_M10_THROUGH_REVISION):
            groups = selected_groups(settings["groups"])
            if tuple(settings["groups"]) != groups:
                raise ValueError("INVALID_M5_M10_SCOPE")
            return groups
        return super().strategies_for(plan)

    def scan(self, path):
        state = super().scan(path)
        gate = read_gate(path)
        state["research_sample_eligibility"] = gate
        # Do not corrupt the raw resumable ledger; this is an evaluation gate.
        if gate["status"] != "ELIGIBLE":
            state = {**state, "invalid": True}
        return state

    def report(self, states, errors, terminal):
        result = super().report(states, errors, terminal)
        result["excluded_research_samples"] = [
            {"market": name, **state["research_sample_eligibility"]}
            for name, state in states.items()
            if state.get("research_sample_eligibility", {}).get("status") != "ELIGIBLE"
        ]
        return result


base.InstantFill = ResearchInstantFill

if __name__ == "__main__":
    base.main()
