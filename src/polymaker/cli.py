"""polymaker command-line interface.

  polymaker scan                 sweep Gamma for political markets -> SQLite
  polymaker markets              rank/browse the catalog
  polymaker markets-add <slug>   append a market to config/markets.toml
  polymaker status               positions / open orders / PnL (reads SQLite)
  polymaker doctor               preflight: wallet auth, balances, WS reachability
  polymaker run [--paper]        start the market maker
  polymaker cancel-all           panic button
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from polymaker import __version__
from polymaker.config import Config

app = typer.Typer(
    name="polymaker",
    help="Maker-only market maker for Polymarket CLOB V2.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


@app.command()
def version() -> None:
    """Print the polymaker version."""
    console.print(f"polymaker {__version__}")


@app.command("rdp-sm-preflight")
def rdp_sm_preflight(
    slug: str = typer.Argument(..., help="exact btc-updown-5m-<start_epoch> slug"),
    config_dir: str = typer.Option("config", help="existing FL config directory"),
) -> None:
    """RDP-SM READ_ONLY safety snapshot; READY never authorizes trading."""
    import json

    from polymaker.logging import configure
    from polymaker.rdp_sm import Check, PreflightResult, SessionStatus, run_preflight

    configure(console=True)  # stderr only; no logs/database/artifacts on disk
    try:
        result = asyncio.run(run_preflight(Config.load(config_dir), slug))
    except Exception as exc:
        result = PreflightResult(checks=[Check(
            "configuration", "BLOCKED", f"INITIALIZATION_FAILED:{type(exc).__name__}")])
    typer.echo(json.dumps(result.to_dict(), allow_nan=False))
    raise typer.Exit(0 if result.status is SessionStatus.READY
                     else 1 if result.status is SessionStatus.REJECTED else 2)


@app.command("rdp-sm-run")
def rdp_sm_run(
    config_dir: str = typer.Option("config", help="existing FL config directory"),
    human: bool = typer.Option(True, "--human/--json-only", help="human logs on stderr; JSON stays on stdout"),
    http_info: bool = typer.Option(False, "--http-info", help="show raw HTTP/component INFO logs for diagnosis"),
    shadow: bool = typer.Option(False, "--shadow", help="start read-only Risk Shadow for this market/session"),
) -> None:
    """Run one current BTC 5m market through M1/M3/M2, close and exit."""
    import json
    import logging

    from polymaker.logging import configure
    from polymaker.rdp_sm_entry import run_current_market
    from polymaker.rdp_sm_logging import HumanRunLog, redact_credentials
    from polymaker.rdp_sm_loop import RunResult

    configure(console=True)
    if not http_info:
        # Console only. Preserve WARNING/ERROR, other loggers and file logs.
        quiet_names = {"httpx", "httpcore"}
        if human:
            quiet_names.update({"execution.filltruth", "state.tracker", "state.store"})
        for handler in logging.getLogger().handlers:
            handler.addFilter(lambda record: record.levelno >= logging.WARNING
                              or (record.name not in quiet_names
                                  and record.name.split(".")[0] not in quiet_names))
    observer = None
    secrets = ()
    try:
        cfg = Config.load(config_dir)
        secrets = (cfg.secrets.pk, cfg.secrets.builder_key,
                   cfg.secrets.builder_secret, cfg.secrets.builder_passphrase)
        if human:
            observer = HumanRunLog(signature_type=cfg.wallet.signature_type,
                                   repair_mode=cfg.rdp_sm.normal_entry_mode == "obi_v1",
                                   secrets=secrets, console_handlers=logging.getLogger().handlers[:])
        result = asyncio.run(run_current_market(cfg, observer=observer, shadow_mode=True)
                             if shadow else run_current_market(cfg, observer=observer))
    except Exception as exc:
        import time

        result = RunResult(None, "", stop_time=time.time(),
                           stop_reason=f"INITIALIZATION_FAILED:{type(exc).__name__}")
    if human:
        if observer is None:
            observer = HumanRunLog(console_handlers=logging.getLogger().handlers[:])
        observer.emit("summary", result.to_dict())
        observer.close()  # Execution resources already closed; bounded display drain.
    typer.echo(json.dumps(redact_credentials(result.to_dict(), secrets), allow_nan=False))
    raise typer.Exit(0 if result.execution_status == "COMPLETED" else 2)


@app.command("rdp-sm-repair-check")
def rdp_sm_repair_check(
    config_dir: str = typer.Option("config", help="existing config directory"),
) -> None:
    """Read local HD/TP/SL configuration; no exchange or order access."""
    import json

    from polymaker.repair_policy import repair_readiness

    cfg = Config.load(config_dir)
    result = repair_readiness(cfg.rdp_sm.repair)
    result["policy_selected"] = (cfg.rdp_sm.normal_entry_mode == "obi_v1"
                                 and cfg.rdp_sm.repair.policy == "hd_tp_sl_v1")
    typer.echo(json.dumps(result, allow_nan=False))
    raise typer.Exit(0 if result["ready_for_live"] and result["policy_selected"] else 2)


@app.command("research-audit")
def research_audit(
    input_path: Path = typer.Argument(..., help="completed Research session or dataset directory"),  # noqa: B008
    output_dir: Path = typer.Option(Path("research_audits"), help="derived audit output root"),  # noqa: B008
    real_sessions_file: Path | None = typer.Option(None, help="optional, non-authoritative LIVE assertions"),  # noqa: B008
    timing_file: Path | None = typer.Option(None, help="optional, non-authoritative timing assertions"),  # noqa: B008
) -> None:
    """Read persisted M4A evidence and report integrity and readiness separately."""
    import json

    from polymaker.research.audit import audit_collection, write_audit

    if not input_path.is_dir():
        raise typer.BadParameter("Research session or dataset directory does not exist")
    artifacts = ("manifest.json", "quality.json", "events.jsonl",
                 "episodes.jsonl", "states.jsonl")
    # A damaged session is still a candidate when any other raw M4A artifact
    # survives. An unrelated directory with no raw artifact is ignored.
    children = sorted(path for path in input_path.iterdir() if path.is_dir())
    candidates = [path for path in children if any((path / name).exists() for name in artifacts)]
    sessions = ([input_path] if any((input_path / name).exists() for name in artifacts)
                or not children else candidates)
    if not sessions:
        raise typer.BadParameter("dataset contains no Research session artifacts")
    try:
        real = (json.loads(real_sessions_file.read_text(encoding="utf-8"))
                if real_sessions_file else None)
        timing = (json.loads(timing_file.read_text(encoding="utf-8"))
                  if timing_file else None)
        if real is not None and not isinstance(real, dict):
            raise ValueError("real-session attestations must be an object")
        if timing is not None and not isinstance(timing, dict):
            raise ValueError("timing evidence must be an object")
        result = audit_collection(sessions, real_sessions=real, timing_evidence=timing)
        target = output_dir / input_path.parent.name / input_path.name
        write_audit(result, target)
    except (OSError, ValueError, TypeError) as exc:
        raise typer.BadParameter(f"audit input/output invalid: {type(exc).__name__}") from exc
    console.print(f"M4A audit: {result['overall']} | integrity {result['integrity']} | "
                  f"collection {result['collection']} | timing {result['timing']['status']} | "
                  f"isolation {result['isolation']['status']}")
    console.print(f"Reports: {target / 'audit.json'}, {target / 'audit_report.md'}")
    raise typer.Exit(0 if result["overall"] == "PASS" else 1 if result["overall"] == "FAIL" else 2)


@app.command()
def scan(
    config_dir: str = typer.Option("config", help="config directory"),
    min_liquidity: float = typer.Option(1000.0, help="minimum market liquidity (USDC)"),
    all_markets: bool = typer.Option(False, "--all", help="include non-rewards markets"),
) -> None:
    """Sweep Gamma for political markets, score, and persist to SQLite."""
    from polymaker.catalog.scanner import ScanConfig, run_scan
    from polymaker.catalog.store import CatalogStore

    cfg = Config.load(config_dir)
    store = CatalogStore(cfg.paths.db)

    async def _go() -> int:
        metas = await run_scan(store, ScanConfig(min_liquidity=min_liquidity, rewards_only=not all_markets))
        return len(metas)

    n = asyncio.run(_go())
    csv_path = Path(config_dir).parent / "markets.csv"
    written = store.export_csv(csv_path)
    console.print(f"[green]Scanned and stored {n} markets.[/green] "
                  f"Wrote [bold]{csv_path}[/bold] ({written} rows) — open it, pick markets, "
                  f"then `polymaker markets-add <slug>`.")
    store.close()


@app.command()
def markets(
    config_dir: str = typer.Option("config", help="config directory"),
    limit: int = typer.Option(25, help="rows to show"),
) -> None:
    """Show the top scored markets from the catalog."""
    from polymaker.catalog.store import CatalogStore

    cfg = Config.load(config_dir)
    store = CatalogStore(cfg.paths.db)
    rows = store.top(limit)
    if not rows:
        console.print("[yellow]Catalog empty. Run `polymaker scan` first.[/yellow]")
        raise typer.Exit()

    table = Table(title="Political markets by score")
    for col in ("score", "reward/day", "rebate/day", "spread", "tick", "neg", "question"):
        table.add_column(col, justify="right" if col != "question" else "left")
    for meta, sc in rows:
        table.add_row(
            f"{sc.score:.2f}", f"{meta.rewards_daily_rate:.0f}", f"{sc.rebate_potential:.0f}",
            f"{sc.spread:.3f}", f"{meta.tick_size:g}", "Y" if meta.neg_risk else "-",
            meta.question[:60],
        )
    console.print(table)
    console.print("\nAdd one with: [bold]polymaker markets-add <slug>[/bold]  (slugs are in the catalog)")


@app.command(name="markets-add")
def markets_add(
    slug: str,
    profile: str = typer.Option("political-longdated", help="strategy profile"),
    config_dir: str = typer.Option("config", help="config directory"),
) -> None:
    """Append a market (by slug) to config/markets.toml."""
    from polymaker.catalog.store import CatalogStore

    cfg = Config.load(config_dir)
    store = CatalogStore(cfg.paths.db)
    meta = store.get_by_slug(slug)
    store.close()
    if meta is None:
        console.print(f"[red]No market with slug {slug!r} in the catalog. Run `polymaker scan`.[/red]")
        raise typer.Exit(1)

    path = Path(config_dir) / "markets.toml"
    block = f'\n[[markets]]\nslug    = "{slug}"\nprofile = "{profile}"\nenabled = true\n'
    with path.open("a") as fh:
        fh.write(block)
    console.print(f"[green]Added[/green] {meta.question[:60]!r} to {path}")


@app.command()
def status(config_dir: str = typer.Option("config", help="config directory")) -> None:
    """Show positions, open orders, and marks from the local state DB."""
    from polymaker.state.store import StateStore

    cfg = Config.load(config_dir)
    store = StateStore(cfg.paths.db)
    snap = store.snapshot()
    console.print(f"[bold]Open orders:[/bold] {snap['open_orders']}")
    positions: dict[str, Any] = snap["positions"]  # type: ignore[assignment]
    if not positions:
        console.print("[dim]No open positions.[/dim]")
    else:
        table = Table(title="Positions")
        table.add_column("token")
        table.add_column("size", justify="right")
        table.add_column("avg", justify="right")
        for tok, p in positions.items():
            table.add_row(tok[:16] + "…", f"{p['size']:.2f}", f"{p['avg_price']:.3f}")
        console.print(table)
    store.close()


@app.command()
def pnl(config_dir: str = typer.Option("config", help="config directory")) -> None:
    """Show PnL from the recorded snapshots (equity, daily PnL, fills)."""
    import sqlite3

    cfg = Config.load(config_dir)
    conn = sqlite3.connect(cfg.paths.db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT ts, equity, net_cash, inventory_value, daily_pnl FROM pnl_snapshots "
        "ORDER BY ts DESC LIMIT 1"
    ).fetchall()
    if not rows:
        console.print("[yellow]No PnL snapshots yet (run the engine first).[/yellow]")
    else:
        r = rows[0]
        color = "green" if r["daily_pnl"] >= 0 else "red"
        console.print(f"[bold]equity:[/bold] {r['equity']:.4f}  "
                      f"[bold]inventory:[/bold] {r['inventory_value']:.4f}  "
                      f"[bold]net cash:[/bold] {r['net_cash']:.4f}")
        console.print(f"[bold]daily PnL:[/bold] [{color}]{r['daily_pnl']:+.4f}[/{color}] pUSD")
    nfills = conn.execute("SELECT COUNT(*) n FROM fills").fetchone()["n"]
    console.print(f"[dim]total fills recorded: {nfills}[/dim]")
    conn.close()


@app.command(name="export-csv")
def export_csv(
    config_dir: str = typer.Option("config", help="config directory"),
    out: str = typer.Option("markets.csv", help="output CSV path"),
    limit: int = typer.Option(500, help="max rows"),
) -> None:
    """Export the scored market catalog to a CSV for easy picking."""
    from polymaker.catalog.store import CatalogStore

    cfg = Config.load(config_dir)
    store = CatalogStore(cfg.paths.db)
    n = store.export_csv(out, limit)
    store.close()
    console.print(f"[green]Wrote {n} markets to {out}.[/green]")


@app.command()
def doctor(config_dir: str = typer.Option("config", help="config directory")) -> None:
    """Preflight checks: config, wallet auth, balance/allowance, WS reachability."""
    from polymaker.doctor import run_doctor

    cfg = Config.load(config_dir)
    ok = asyncio.run(run_doctor(cfg, console))
    raise typer.Exit(0 if ok else 1)


@app.command()
def run(
    config_dir: str = typer.Option("config", help="config directory"),
    paper: bool = typer.Option(False, "--paper", help="paper mode: full pipeline, no orders posted"),
) -> None:
    """Start the market maker."""
    from polymaker.engine import Engine
    from polymaker.logging import configure

    cfg = Config.load(config_dir)
    configure(json_file=Path(cfg.paths.log_dir) / ("paper.jsonl" if paper else "live.jsonl"))
    if cfg.engine.loop == "uvloop":
        try:
            import uvloop

            uvloop.install()
        except Exception:  # noqa: BLE001
            pass

    engine = Engine(cfg, paper=paper)

    async def _go() -> None:
        try:
            await engine.run_forever()
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await engine.shutdown()

    console.print(f"[bold green]Starting polymaker[/bold green] ({'PAPER' if paper else 'LIVE'})…")
    try:
        asyncio.run(_go())
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopped.[/yellow]")


@app.command(name="btc5m-paper")
def btc5m_paper(
    config_dir: str = typer.Option("config", help="config directory"),
    once: bool = typer.Option(False, "--once", help="run one 5-minute market session"),
    shutdown_buffer_s: float = typer.Option(15.0, "--shutdown-buffer-s", min=0.0,
                                            help="stop quoting this many seconds before expiry"),
) -> None:
    """Run BTC 5-minute markets using PAPER mode only."""
    from polymaker.btc5m import run_btc5m_paper
    from polymaker.logging import configure

    cfg = Config.load(config_dir)
    configure(json_file=Path(cfg.paths.log_dir) / "paper.jsonl")
    console.print("[bold green]Starting BTC5m polymaker[/bold green] (PAPER)…")
    try:
        asyncio.run(run_btc5m_paper(config_dir, once=once,
                                    shutdown_buffer_s=shutdown_buffer_s))
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopped.[/yellow]")


@app.command(name="favorite-first-live")
def favorite_first_live(
    config_dir: str = typer.Option("config", help="config directory"),
    once: bool = typer.Option(False, "--once", help="arm exactly one live BTC5m market"),
    preflight_only: bool = typer.Option(False, "--preflight-only",
                                        help="never arm or place; may cancel selected-market orders"),
    research: bool = typer.Option(False, "--research", help="capture M4A causal events"),
) -> None:
    """Run one controlled FavoriteFirst live market or stop after preflight."""
    from polymaker.favorite_live import LiveResult, run_favorite_first_live
    from polymaker.logging import configure
    from polymaker.research.observer import ResearchConfig

    if once == preflight_only:
        raise typer.BadParameter("choose exactly one of --once or --preflight-only")
    cfg = Config.load(config_dir)
    configure(json_file=Path(cfg.paths.log_dir) / "favorite-first-live.jsonl")
    result = asyncio.run(run_favorite_first_live(
        config_dir, console, preflight_only=preflight_only,
        research_config=ResearchConfig(enabled=research)))
    console.print(f"Result: {result.value}")
    raise typer.Exit(0 if result in (LiveResult.PREFLIGHT_PASS, LiveResult.COMPLETED,
                                      LiveResult.UNPAIRABLE_RESIDUAL) else 1)


@app.command(name="favorite-first-report")
def favorite_first_report(
    last: int = typer.Option(10, "--last", min=1, help="recent completed runs"),
    config_dir: str = typer.Option("config", help="config directory"),
) -> None:
    """Summarize completed FavoriteFirst runs from structured run artifacts."""
    from polymaker.favorite_report import aggregate

    cfg = Config.load(config_dir)
    console.print(aggregate(Path(cfg.paths.log_dir) / "runs", last))


@app.command(name="settlement-sweep")
def settlement_sweep(
    once: bool = typer.Option(False, "--once", help="execute one live settlement sweep"),
    preflight_only: bool = typer.Option(False, "--preflight-only",
                                        help="read wallet state without any settlement transaction"),
    config_dir: str = typer.Option("config", help="config directory"),
) -> None:
    """Recycle funder-owned complete sets and resolved outcomes; never place orders."""
    if once == preflight_only:
        raise typer.BadParameter("choose exactly one of --once or --preflight-only")
    from polymaker.settlement_sweep import preflight, sweep

    cfg = Config.load(config_dir)
    if preflight_only:
        row = preflight(cfg, print_plan=console.print)
        console.print(f"Settlement preflight: {row['status']} | {row['artifact_dir']}")
        raise typer.Exit(0 if row["status"] == "PREFLIGHT_PASS" else 1)
    row = sweep(cfg, print_plan=console.print)
    console.print(f"Settlement: {row['status']} | {row['artifact_dir']}")
    for gate, value in row["static_gates"].items():
        console.print(f"{gate}: {value}")
    raise typer.Exit(0 if row["status"] in ("CONFIRMED", "NOTHING_TO_DO") else 1)


@app.command()
def livetest(
    config_dir: str = typer.Option("config", help="config directory"),
    notional: float = typer.Option(5.0, help="order notional in USDC"),
) -> None:
    """Live wallet round-trip: place a deep post-only order and cancel it (~$5)."""
    from polymaker.livetest import run_livetest

    cfg = Config.load(config_dir)
    ok = asyncio.run(run_livetest(cfg, console, notional))
    raise typer.Exit(0 if ok else 1)


@app.command()
def filltest(config_dir: str = typer.Option("config", help="config directory")) -> None:
    """Wait for one low-risk BTC5m maker fill and verify its WS and position state."""
    from polymaker.filltest import run_filltest
    from polymaker.logging import configure

    cfg = Config.load(config_dir)
    configure(json_file=Path(cfg.paths.log_dir) / "filltest.jsonl")
    ok = asyncio.run(run_filltest(cfg))
    raise typer.Exit(0 if ok else 1)


@app.command()
def moneydoctor(
    config_dir: str = typer.Option("config", help="config directory"),
) -> None:
    """LIVE trading self-test: rest a limit, then market buy + sell (spends a little)."""
    from polymaker.moneydoctor import run_moneydoctor

    cfg = Config.load(config_dir)
    ok = asyncio.run(run_moneydoctor(cfg, console))
    raise typer.Exit(0 if ok else 1)


@app.command(name="cancel-all")
def cancel_all(config_dir: str = typer.Option("config", help="config directory")) -> None:
    """Cancel all open orders for the wallet (panic button)."""
    from polymaker.execution.gateway import ExecutionGateway

    cfg = Config.load(config_dir)
    gw = ExecutionGateway(cfg)

    async def _go() -> None:
        await gw.connect()
        await gw.cancel_all()

    asyncio.run(_go())
    console.print("[green]Sent cancel-all.[/green]")


if __name__ == "__main__":
    app()
