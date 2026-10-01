"""Isolated research persistence skeleton; durability/audit follow in M4A.4/5."""

from __future__ import annotations

from pathlib import Path
from queue import Empty
from threading import Event, Thread
from typing import TextIO

from polymaker.research.observer import BoundedResearchQueue, ResearchItem
from polymaker.research.schema import Episode, ResearchEvent, StateSnapshot


class ResearchWriter:
    """Owns JSONL files on a dedicated thread; never receives trading objects."""

    def __init__(self, output_root: Path, dataset_id: str, session_id: str,
                 queue: BoundedResearchQueue) -> None:
        for name in (dataset_id, session_id):
            if not name or name in (".", "..") or "/" in name or "\\" in name:
                raise ValueError("dataset and session IDs must be single path components")
        self.directory = Path(output_root) / dataset_id / session_id
        self.queue = queue
        self._stop = Event()
        self._thread: Thread | None = None
        self.error: Exception | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("writer already started")
        self.directory.mkdir(parents=True, exist_ok=True)
        self._thread = Thread(target=self._run, name="research-writer", daemon=True)
        self._thread.start()

    def close(self, timeout: float = 5.0) -> bool:
        if self._thread is None:
            raise RuntimeError("writer not started")
        self.queue.close_producer()
        self._stop.set()
        self._thread.join(timeout)
        return not self._thread.is_alive() and self.error is None

    def _run(self) -> None:
        files: dict[str, TextIO] = {}
        try:
            for name in ("events", "episodes", "states"):
                files[name] = (self.directory / f"{name}.jsonl").open("a", encoding="utf-8")
            while not self._stop.is_set() or not self.queue.empty():
                try:
                    item = self.queue.get(timeout=0.05)
                except Empty:
                    continue
                try:
                    self._write(item, files)
                finally:
                    self.queue.task_done()
        except Exception as exc:
            self.error = exc
        finally:
            for file in files.values():
                file.close()

    @staticmethod
    def _write(item: ResearchItem, files: dict[str, TextIO]) -> None:
        if isinstance(item, ResearchEvent):
            name = "events"
        elif isinstance(item, Episode):
            name = "episodes"
        elif isinstance(item, StateSnapshot):
            name = "states"
        else:
            raise TypeError("unsupported research record")
        files[name].write(item.model_dump_json() + "\n")
