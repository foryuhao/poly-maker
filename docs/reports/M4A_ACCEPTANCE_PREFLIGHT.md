# M4A Acceptance Preflight

Date: 2026-10-02
Role: Architect; read-only runtime inspection followed by documentation-only blocker resolution.
Checkpoint: `m4a5-pass` / `4d75a19bf4f4e0a967d711cb57f651453f2fdd30`
Conclusion: **BLOCKED**
Actual M4A readiness: **NOT READY**
M4B: **NOT AUTHORIZED**

This is acceptance-preflight evidence, not an implementation Review or a new PASS/FAIL verdict on M4A.5. M4A.1–M4A.5 remain independently PASSed and checkpointed. The current auditor correctly refuses unsupported acceptance claims. The current live integration nevertheless cannot produce an acceptance-eligible session: its configuration identities disagree, and four required evidence contracts are absent.

## Scope and verification

Inspected `AGENTS.md`, current status, Research design, M4/M4A architectures, M4A.5 Implementation Report and complete Review history. Inspected the FavoriteFirst live wrapper, Engine attachment, Research schema/capture/lifecycle/writer/auditor, execution gateway, authoritative state tracker, marketdata service and CLI. Git startup showed clean `m4-dynamic-ev`, HEAD at the implementation checkpoint above; implementation commit `d375830` and checkpoint-status commit `4d75a19` are present.

No completed dataset was audited. Neither `research_data/` nor `research_acceptance/` existed in this workspace at inspection. Historical test results are evidence of implementation testing, not real acceptance measurements. No test suite, live command, network request, order, cancellation or transaction was executed. A pure in-memory config hash calculation used the actual production expressions; it created no Research artifacts.

## Blocker A — Configuration identity mismatch

Manifest producer: `src/polymaker/favorite_live.py`, `effective_config` (line 82) and Research initialization (lines 607–616).

```python
values = asdict(config)
values.pop("probation_passive_offset_ticks")
values["first_leg_risk_window_s"] = values.pop("probation_max_s")
# wrapper:
json.dumps(public_config, sort_keys=True, separators=(",", ":")).encode()
```

The wrapper SHA-256 hashes these UTF-8 bytes and puts the digest in both `manifest.strategy_config_id` and `config_sha256`. `effective_strategy_config` contains the normalized public object. It excludes the deprecated, behaviorally unused probation offset and uses the new risk-window name; it includes the remaining effective strategy dataclass fields. It does not include runtime timestamps or credentials.

Episode identity producer: `src/polymaker/engine.py`, `attach_favorite_first` (line 590):

```python
hashlib.sha256(json.dumps(asdict(config), sort_keys=True).encode()).hexdigest()
```

This independently hashes the raw dataclass, retaining `probation_max_s` and `probation_passive_offset_ticks`, without the wrapper's alias, and using default JSON spaces. Engine passes this digest to EpisodeLifecycle; lifecycle copies it into every Episode. Thus both **field projection and byte serialization differ**. Research does not subsequently reconcile them. `research/audit.py:_episode_checks` (lines 580–581) correctly requires Episode/manifest config identity and session association to agree.

Exact probe on the unchanged `CONTROLLED_CONFIG`:

| Production expression | Bytes | SHA-256 |
|---|---:|---|
| Wrapper effective object, compact JSON | 385 | `5709c9932ff81a3286828d7daae1b7314118a5b0289c10390b47d5956bef2ff7` |
| Engine raw dataclass, default JSON spacing | 443 | `9aa0693fbe98e3da7a38a8a0426b89743b680f26bf6987e9cf837ee96776878c` |

For clarity, hashing the raw dataclass with **compact** JSON instead gives `f4078c0eb922771743e4e06be92519de17406e32d69304de9357e4d5bb0fa8bb`; that simplified comparison is not the actual Engine expression. The mismatch conclusion does not depend on which of these raw encodings is used.

Required change: one canonical effective-config helper and immutable runtime/session identity passed downstream. Keep the auditor's consistency check; do not repair old artifacts or loosen equality. The new `favorite-first-effective-v1` contract is specified in architecture revision AE-1, section A.

## Blocker B — Trusted LIVE provenance

Current evidence:

- `favorite_live.py` constructs Engine with `paper=False` (line 634), but manifest v1 (`research/schema.py:DatasetManifest`) contains no runtime LIVE/gateway/armed attestation contract.
- `execution/gateway.py:place` emits a real `ORDER_SUBMIT` with `paper=False`, request correlation and subsequent ACK outcome/order IDs. Its paper branch emits `paper=True` and synthetic acknowledgments. These are useful reinforcing execution observations, not a complete accepted provenance contract.
- Owned registration identifies FIRST/PAIR orders. `state/tracker.py` emits authoritative accepted/quarantined Fill Truth; `research/lifecycle.py` starts an Episode only from a positive accepted owned FIRST BUY fill and accumulates fills by identity. This establishes economic origin within the recording, not independently its LIVE origin.
- `research/audit.py:audit_collection` explicitly returns LIVE and collection `UNESTABLISHED`, eligible Episode count zero, and classifies optional caller inputs as `CALLER_ASSERTION` with `accepted_for_gates=False`.

**LIVE ATTESTATION GAP.** No existing artifact/field is accepted by the auditor as trusted LIVE attestation. A caller cannot make the current audit PASS through `--real-sessions-file`. A real FavoriteFirst run today cannot complete the full owned order → Fill Truth → FIRST Episode → verified LIVE → collection eligibility chain without a runtime/auditor extension, even after fixing A.

Required change: runtime-produced context binding actual Engine/gateway modes, armed transition, clean source build, canonical config and non-secret account/venue identity, reinforced by existing real submit/ACK/owned-order/accepted-fill evidence. Define a custody-based runtime trust boundary explicitly; a hash alone does not authenticate a file owner. AE-1 sections B/C specify the minimum contract and legacy handling.

## Blocker C — Research callback latency evidence

The capture/observer paths record receipt/availability/monotonic causal clocks but do not measure complete Research callback duration. Payload construction can happen before `emit`; timing only writer admission would undercount callback work. Writer health records counts/failures/queue state, not callback p50/p95/p99/max. No trusted real-traffic callback measurement artifact is persisted or consumed by the auditor.

**CALLBACK LATENCY EVIDENCE GAP.** Current timing acceptance remains `UNESTABLISHED`; excellent caller-supplied timing numbers cannot change it. Unit tests and asynchronous writer structure do not establish real peak-traffic p99 <1ms.

Required change: instrument the outer synchronous Research observation boundary with a monotonic nanosecond timer, bounded all-invocation aggregation, explicit completeness and runtime/session provenance. Persist measurements separately from persistence health; validate sufficient live and recorded-peak samples. AE-1 section D fixes the boundary, quantiles and sample requirements.

## Blocker D — Incremental loop-lag evidence

`marketdata/service.py` computes `processing_lag_ms` from queue receipt to completed processing and periodically logs it. This is neither a loop scheduling probe nor an OFF/ON causal increment. No paired baseline, controlled workload, complete scheduling samples or trusted persisted comparison exists. The current audit therefore cannot calculate the required incremental loop-lag p99 <=5ms.

**LOOP-LAG EVIDENCE GAP.** Existing marketdata lag cannot substitute for the missing experiment.

Required change: an offline, non-trading controlled Research OFF/ON experiment on identical recorded peak traffic, matching build/settings/environment, paired absolute loop deadlines and persisted raw samples. No real-money A/B replication. AE-1 section E defines the comparison and its applicability limits; auditing consumes recorded results and never implicitly reruns a benchmark.

## Blocker E — Research/live isolation evidence

Current code and deterministic engineering tests establish structural protections: Research has no submit/cancel authority, bounded admission, asynchronous persistence and explicit failure handling. Those remain prerequisites. The current schema has no trusted controlled comparison trace/case-result contract, and `audit_collection` returns isolation `UNESTABLISHED` rather than accepting caller trading traces.

**ISOLATION EVIDENCE GAP.** Neither a caller's “unchanged” label nor a textual test count establishes the required persisted acceptance evidence. This gap does not demonstrate that current Research changes trading behavior.

Required change: persist a reviewed offline deterministic comparison using the real Engine/policy paths and a non-network execution sink. Compare decisions/order intents/authoritative state with Research OFF, ON and required Research faults. Existing structural tests remain authoritative for their tested invariants; the comparison adds inspectable acceptance evidence. No statistical strategy A/B study, profit comparison or live fault injection. AE-1 section F defines required cases and verdicts.

## Capabilities already present and acceptance limits

- Lifecycle records causal FIRST starts, material fill/order/terminal snapshots, connected 1-second cadence and continued observation through **market start +300s**; interruption/GAP/writer failure is explicit and may censor. It is not a new 300 seconds after first fill.
- The auditor reconstructs as-known fills/inventory/orders/books/ticks, checks semantic terminal snapshots, computes expected/valid/invalid/excluded cadence and unexplained gaps, and preserves raw artifacts. Existing 99% and >2s gates remain intact; A still prevents the unmodified live wrapper's otherwise normal session from passing identity validation.
- Dataset discovery uses surviving Research artifact markers and reports damaged candidate sessions; UTC market-days derive from market start. Candidate Episodes are deduplicated, but actual verified LIVE collection counts stay zero until B is resolved. There is no qualifying 20-Episode/3-day collection demonstrated here.
- The final manifest is the COMPLETE authority. `quality.json` is persistence health; it is not a LIVE/performance certificate. Preserve the reviewed final-rename durability limitation rather than claim stronger crash guarantees.

## Current command surface — inspected, not executed

The actual one-market command is:

```bash
uv run polymaker favorite-first-live --config-dir livecfg --once --research
```

`livecfg` is the existing live configuration directory; CLI default is `config`. Exactly one of `--once` or `--preflight-only` is required. **`--preflight-only` can cancel selected-market orders and is not a read-only probe.** This report authorizes neither command.

Research output is `research_data/m4a/<session_id>/`, with `manifest.json`, `events.jsonl`, `episodes.jsonl`, `states.jsonl`, `quality.json`. Session ID uses the wrapper run ID: UTC selection timestamp `%Y%m%dT%H%M%S%fZ` plus market slug. Current CLI exposes `--research`, not dataset/session-root override flags.

Current single-session and dataset audit syntax:

```bash
uv run polymaker research-audit research_data/m4a/<session_id> --output-dir research_audits
uv run polymaker research-audit research_data/m4a --output-dir research_audits
```

These read raw evidence and write separate derived audit outputs. None was executed here. Optional `--real-sessions-file` and `--timing-file` are non-authoritative and cannot fill any blocker. The new engineering-runner command and evidence locator in AE-1 are **future implementation**, not current functionality.

## Decision and handoff

Conclusion remains **BLOCKED**, rather than merely EVIDENCE_GAP: A is an actual runtime/audit identity incompatibility; B–E prevent mandatory acceptance evidence from being established even after that correction. This finding preserves the historical M4A.5 implementation PASS and distinguishes it from real M4A readiness.

Resolution specification: `docs/architecture/M4A_ARCHITECTURE.md`, Acceptance Evidence Revision AE-1. Implement sequentially: M4A-A1 canonical identity; M4A-A2 runtime/controlled evidence; M4A-A3 auditor consumption; then separately authorized M4A-A4 single-session smoke. Every implementation requires independent Review and a complete reviewed checkpoint. First predecessor remains `m4a5-pass`; subsequent work uses the latest reviewed corrective baseline.

Next authorized action: **Worker M4A-A1 — Canonical Config Identity Repair only**. Do not start multi-day collection, increase trading size, implement M4B or infer Dynamic EV edge. Real smoke requires separate explicit authorization after A1–A3. No production/test behavior changed in this session. Architect output is limited to this report, canonical architecture and current status.
