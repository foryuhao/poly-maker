"""Full socket-to-coordinator scheduling under an already-buffered public burst."""
import asyncio
import json
import time
from collections import deque

import pytest

from m1234_rr1_fixtures import rr1_events
from polymaker.m1234_sim import runner
from polymaker.m1234_sim.engine import market_meta
from polymaker.m1234_sim.events import read_events


@pytest.mark.parametrize('payload_kind', ['heartbeat', 'depth'])
async def test_socket_burst_drains_through_native_market_without_overflow(tmp_path, monkeypatch, payload_kind):
    import polymaker.rdp_sm_marketdata as md

    definition = rr1_events('normal')[0]['definition']
    settlement = next(e['raw'] for e in rr1_events('normal') if e['kind'] == 'SETTLEMENT')
    settlement.update(closed=False, resolutionStatus='pending', outcomePrices=['.5', '.5'])
    total = 10000
    epoch = int(definition['market']['slug'].rsplit('-', 1)[1])
    tasks = []

    class Stream:
        remaining = total

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def send(self, raw):
            return None

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not tasks:
                tasks.append(asyncio.current_task())
            if self.remaining == 0:
                await asyncio.Event().wait()
            self.remaining -= 1
            if payload_kind == 'depth':
                if self.remaining == total - 1:
                    raw = rr1_events('normal')[2]['raw']
                    for row in raw:
                        row['timestamp'] = str(time.time() * 1000)
                    return json.dumps(raw)
                return json.dumps({'event_type':'price_change','market':definition['market']['condition_id'],
                    'timestamp':str(time.time()*1000), 'price_changes':[{'asset_id':'1','price':'.495',
                        'size':str(70+self.remaining%2),'side':'BUY','best_bid':'.495','best_ask':'.515'}]})
            return 'PONG'

    class API:
        async def clock(self):
            now = time.monotonic()
            return [epoch, now, now]

        async def ticks(self, tokens):
            return definition['ticks']

        async def market(self, slug):
            return market_meta(definition['market'])

        async def raw_market(self, slug, **kwargs):
            return settlement

        async def close(self):
            pass

    monkeypatch.setattr(md.websockets, 'connect', lambda *a, **k: Stream())
    monkeypatch.setattr(runner, 'VirtualPublicAPI', API)
    monkeypatch.setattr(runner, 'OUTPUT', tmp_path)
    monkeypatch.setattr(asyncio.get_running_loop(), 'add_signal_handler', lambda *a: None)
    cfg = runner.settings('config/m1234-sim-24h.toml', 'config/m1234-sim-contract.json')
    cfg.update(max_markets=1, max_seconds=900, min_free_disk_mib=512)
    run = runner.LiveRun(tmp_path/'run', cfg)
    original_record = run.record

    async def record(engine, kind, **payload):
        if kind == 'INIT':
            payload['definition']['artificial_fixture'] = 'BUFFERED_SOCKET_COORDINATOR_BURST'
        event = await original_record(engine, kind, **payload)
        if kind == 'FRAME' and engine.coverage['frames'] == total:
            run.stop.set()
        return event

    run.record = record
    result = await asyncio.wait_for(run.run(), timeout=90)
    assert result['reason'] == 'MANUAL_STOP'
    assert run.engines[0].coverage['frames'] == total
    assert run.engines[0].coverage['max_input_backlog'] < 2048
    assert all(task.done() for task in tasks)
    assert not result['cleanup_errors'] and not result['recording_errors']
    events = list(read_events(run.engines[0].directory/'input.jsonl.gz'))
    assert sum(e['kind'] == 'FRAME' for e in events) == total
    assert all(e.get('reason') not in {'WS_QUEUE_OVERFLOW','SIM_CAPTURE_QUEUE_OVERFLOW'} for e in events)
    (tmp_path/'burst-result.json').write_text(json.dumps({
        'frames':total,'coverage':run.engines[0].coverage,'real_orders':result['real_orders']},indent=2)+'\n')


@pytest.mark.parametrize('current', [None, 5.0])
def test_watermark_preserves_frontier_post_pop_head_and_full_count(current):
    values = [2.0, *[5 + i / 10000 for i in range(2048)], 5.0]
    old = runner.pending_watermark(iter(values), current=current)
    compact = runner.pending_watermark(iter(values), current=current, compact=True)
    assert compact['pending'] == old['pending'][:2]
    assert compact['pending_count'] == len(old['pending'])
    assert min(compact['pending']) == min(old['pending'])
    assert compact['pending'][1:] == old['pending'][1:2]
    assert len(json.dumps(compact)) < len(json.dumps(old)) / 100


@pytest.mark.parametrize('current', [None, 0., 5., 5.1, 100.])
def test_fifo_heads_match_full_watermark_including_equal_receipts(current):
    first = deque([2., 5., 5., 6., 8.])
    second = deque([5 + i / 10000 for i in range(2048)])
    expected = runner.pending_watermark([*first, *second], current=current, compact=True)
    assert runner.fifo_watermark(first, second, current=current) == expected


def test_fifo_watermark_does_not_scan_the_backlog():
    class HeadsOnly(deque):
        def __iter__(self):
            for index, value in enumerate(super().__iter__()):
                assert index < 2, 'scanned receipt beyond the required two heads'
                yield value

    queue = HeadsOnly(range(1, 2049))
    assert runner.fifo_watermark(queue, current=0) == {'pending': [0, 1], 'pending_count': 2049}


def test_capture_queue_frame_frontier_tracks_mixed_events_and_full_rejection():
    queue = runner.CaptureQueue(3)
    queue.put_nowait(('CONNECT', {}))
    queue.put_nowait(('FRAME', {'received_mono': 1.}))
    queue.put_nowait(('FRAME', {'received_mono': 2.}))
    with pytest.raises(asyncio.QueueFull):
        queue.put_nowait(('FRAME', {'received_mono': 3.}))
    assert list(queue.frame_receipts) == [1., 2.]
    assert queue.get_nowait()[0] == 'CONNECT'
    assert list(queue.frame_receipts) == [1., 2.]
    queue.get_nowait()
    assert list(queue.frame_receipts) == [2.]
    queue.get_nowait()
    assert not queue.frame_receipts


async def test_disconnect_failure_still_cancels_all_socket_tasks(monkeypatch):
    import polymaker.rdp_sm_marketdata as md

    definition = rr1_events('normal')[0]['definition']
    captured = []

    class Stream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def send(self, raw):
            pass

        def __aiter__(self):
            return self

        async def __anext__(self):
            captured.append(asyncio.current_task())
            await asyncio.Event().wait()

    monkeypatch.setattr(md.websockets, 'connect', lambda *a, **k: Stream())
    feed = runner.CaptureFeed(market_meta(definition['market']), asyncio.Queue(2))
    task = asyncio.create_task(feed._connection())
    await asyncio.sleep(.02)

    def failed(reason):
        raise RuntimeError('ARTIFICIAL_DISCONNECT_FAILURE')

    feed.invalidate = failed
    task.cancel()
    with pytest.raises(RuntimeError, match='DISCONNECT_FAILURE'):
        await task
    assert captured and all(t.done() for t in captured)
    assert feed.ws is None
