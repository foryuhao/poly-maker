"""Offline durability, backpressure and lossless batching for the M1 journal."""

# ruff: noqa: ASYNC240 -- Isolated temporary journal assertions.

import asyncio
import json
import os
from threading import Event

import pytest

from polymaker.m1_live_journal import M1RiskJournal
from polymaker.rdp_sm_writer import AsyncRiskWriter

SID = 'm1_live_' + 'a' * 32
FUNDER = '0x' + '1' * 40


async def test_burst_keeps_every_record_and_syncs_each_batch(tmp_path, monkeypatch):
    backing = M1RiskJournal(tmp_path, FUNDER, session_id=SID)
    entered, release = Event(), Event()
    syncs = []
    real_sync = os.fsync

    def sync(fd):
        syncs.append(fd)
        if len(syncs) == 1:
            entered.set()
            assert release.wait(3)
        real_sync(fd)

    monkeypatch.setattr(os, 'fsync', sync)
    writer = AsyncRiskWriter(backing, batch_size=128)
    try:
        writer.write('sm_m1_signal', {'session_id': SID, 'payload': {'index': 0}}, 0.)
        assert await asyncio.to_thread(entered.wait, 3)
        for i in range(1, 129):
            writer.write('sm_m1_signal', {'session_id': SID, 'payload': {'index': i}}, float(i))
        flushed = asyncio.create_task(writer.flush())
        await asyncio.sleep(0)
        assert not flushed.done() and writer.written == 0
        release.set()
        await asyncio.wait_for(flushed, 3)
        rows = [json.loads(line) for line in tmp_path.joinpath(
            f'rdp_sm_{FUNDER}_{SID}.jsonl').read_text().splitlines()]
        assert [r['data']['payload']['index'] for r in rows] == list(range(129))
        assert [r['data']['writer_sequence'] for r in rows] == list(range(1, 130))
        assert len(syncs) == 2 and backing.sequence == 129
        assert writer.ready and writer.failure is None and writer.dropped == 0
    finally:
        release.set()
        await asyncio.to_thread(writer.close)
    resumed = M1RiskJournal(tmp_path, FUNDER, session_id=SID, resume=True)
    try:
        assert resumed.sequence == resumed.last_writer_sequence == 129
    finally:
        resumed.close()


async def test_failed_batch_never_acknowledges_durability(tmp_path, monkeypatch):
    backing = M1RiskJournal(tmp_path, FUNDER, session_id=SID)
    entered, release = Event(), Event()

    def fail_sync(fd):
        entered.set()
        assert release.wait(3)
        raise OSError('offline disk failure')

    monkeypatch.setattr(os, 'fsync', fail_sync)
    writer = AsyncRiskWriter(backing, batch_size=128)
    try:
        committed = asyncio.create_task(writer.durable(
            'sm_prepared', {'session_id': SID, 'payload': {'critical': True}}, 1.))
        assert await asyncio.to_thread(entered.wait, 3)
        flushed = asyncio.create_task(writer.flush())
        await asyncio.sleep(0)
        assert not committed.done() and not flushed.done()
        release.set()
        with pytest.raises(OSError, match='offline disk failure'):
            await asyncio.wait_for(committed, 3)
        with pytest.raises(RuntimeError, match='ASYNC_JOURNAL_FAILED'):
            await asyncio.wait_for(flushed, 3)
        assert not writer.ready and writer.written == 0
    finally:
        release.set()
        await asyncio.to_thread(writer.close)


async def test_batch_writer_preserves_existing_pressure_watermark(tmp_path, monkeypatch):
    backing = M1RiskJournal(tmp_path, FUNDER, session_id=SID)
    entered, release = Event(), Event()
    real_sync = os.fsync

    def blocked_sync(fd):
        entered.set()
        assert release.wait(3)
        real_sync(fd)

    monkeypatch.setattr(os, 'fsync', blocked_sync)
    writer = AsyncRiskWriter(backing, batch_size=128)
    try:
        writer.write('sm_m1_signal', {'session_id': SID}, 0.)
        assert await asyncio.to_thread(entered.wait, 3)
        for _ in range(3967):
            writer.write('sm_m1_signal', {'session_id': SID}, 1.)
        assert writer.ready and writer.queue.maxsize == 4096
        writer.write('sm_m1_signal', {'session_id': SID}, 1.)
        assert writer.queue.qsize() == 3968 and not writer.ready
        release.set()
        await asyncio.wait_for(writer.flush(), 3)
        assert writer.accepted == writer.written and writer.failure is None
    finally:
        release.set()
        await asyncio.to_thread(writer.close)


def test_batch_identity_conflict_writes_nothing(tmp_path):
    backing = M1RiskJournal(tmp_path, FUNDER, session_id=SID)
    try:
        with pytest.raises(Exception, match='SESSION_JOURNAL_IDENTITY_CONFLICT'):
            backing.write_batch([
                ('sm_m1_signal', {'session_id': SID}, 1.),
                ('sm_prepared', {'session_id': 'other'}, 2.),
            ])
        assert tmp_path.joinpath(f'rdp_sm_{FUNDER}_{SID}.jsonl').read_bytes() == b''
    finally:
        backing.close()
