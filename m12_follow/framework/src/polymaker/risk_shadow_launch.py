"""Optional CLI startup handoff. Only public IDs/paths enter the Observer process."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from queue import Queue
from threading import Thread


def launch_shadow(cfg, meta, session_id):
    # No key/SDK/Gateway object is retained by the worker or child.
    journal = (
        Path(cfg.paths.journal_dir) / f"rdp_sm_{cfg.secrets.browser_address.lower()}_{session_id}.jsonl"
    ).absolute()
    output = Path("research_outputs/rdp_risk_shadow").absolute()
    argv = [
        sys.executable,
        "-m",
        "polymaker.risk_shadow",
        "--enable",
        "--slug",
        meta.slug,
        "--session-id",
        session_id,
        "--journal",
        str(journal),
        "--journal-from-start",
        "--output-root",
        str(output),
        "--expected-condition-id",
        meta.condition_id,
        "--expected-yes-token-id",
        meta.yes.token_id,
        "--expected-no-token-id",
        meta.no.token_id,
    ]
    environment = {key: os.environ[key] for key in ("PATH", "LANG") if key in os.environ}
    queue = Queue(maxsize=1)
    queue.put_nowait((argv, environment, output, session_id, meta.slug))
    # Parent only enqueues startup metadata. Filesystem/process I/O is on this worker.
    worker = Thread(target=_start, args=(queue,), name="risk-shadow-launch", daemon=False)
    worker.start()
    return worker


def _start(queue):
    argv, environment, output, session_id, slug = queue.get_nowait()
    log = logging.getLogger("polymaker.risk_shadow")
    try:
        directory = output / "launch_logs"
        directory.mkdir(parents=True, exist_ok=True)
        logfile = directory / f"{session_id}.log"
        with logfile.open("x") as handle:
            child = subprocess.Popen(
                argv,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=handle,
                start_new_session=True,
                close_fds=True,
            )
        # The child has its own market deadline. Neither exit nor errors affect trading.
        log.warning(
            "Risk Shadow STARTED market=%s session=%s pid=%s log=%s",
            slug,
            session_id,
            child.pid,
            logfile,
        )
    except Exception as exc:
        log.error(
            "SHADOW_ERROR launch=%s market=%s session=%s", type(exc).__name__, slug, session_id
        )
    finally:
        queue.task_done()
