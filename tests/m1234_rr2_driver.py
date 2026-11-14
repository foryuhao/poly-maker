"""Artificial persistent snapshot fault around the existing simulation CLI.

The hook changes only storage behavior and commits one known prefix. It never
changes a signal, model outcome, input identity check or source file.
"""
import argparse
import json
import runpy
import sys
from pathlib import Path

from polymaker.m1234_sim.engine import Engine
from polymaker.m1234_sim.events import read_events, write_json
from polymaker.m1234_sim.report import read_state
from polymaker.m1234_sim.store import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--fail-at', required=True, type=int)
    args = parser.parse_args()
    tape = list(read_events(args.input))
    assert tape[0]['definition']['artificial_fixture']
    assert 1 < args.fail_at <= len(tape)
    directory = Path(args.output)
    directory.mkdir(parents=True, exist_ok=False)
    engine = None
    attempts = 0
    original_snapshot = Store.snapshot
    original_ingest = Engine.ingest

    def persistent_failure(self, name, payload):
        nonlocal attempts
        if payload.get('input_seq', 0) >= args.fail_at:
            attempts += 1
            raise OSError('ARTIFICIAL_PERSISTENT_SNAPSHOT_WRITE_FAILURE')
        return original_snapshot(self, name, payload)

    async def ingest(self, event):
        nonlocal engine
        engine = self
        await original_ingest(self, event)
        if event['seq'] == args.fail_at - 1:
            self.persist()
            write_json(directory / 'before-fault.json', read_state(self.store.path))

    Store.snapshot = persistent_failure
    Engine.ingest = ingest
    write_json(directory / 'injection-config.json', {
        'artificial_storage_fault': True, 'fail_at_input_seq': args.fail_at,
        'persist_through': 'failed_output, report generation, rollback and process exit',
        'entrypoint': 'python -m polymaker.m1234_sim replay',
        'input': str(Path(args.input).resolve()), 'source_identity_bypass': False})
    sys.argv = ['polymaker.m1234_sim', 'replay', '--input', args.input, '--output', args.output]
    try:
        runpy.run_module('polymaker.m1234_sim', run_name='__main__')
    finally:
        if engine is not None:
            memory = engine.snapshot()
            prefix = read_state(engine.store.path)
            engine.store.db.rollback()
            engine.store.db.close()
            engine.closed = True
            write_json(directory / 'injection-result.json', {
                'artificial_storage_fault': True, 'snapshot_failure_attempts': attempts,
                'writer_still_injected': Store.snapshot is persistent_failure,
                'memory_state': memory, 'committed_prefix': prefix,
                'binding': engine.store.identity,
                'failure': json.loads((directory / 'failure.json').read_text()),
                'publication': json.loads((directory / 'latest.json').read_text())})


if __name__ == '__main__':
    main()
