"""Isolated CPU signing for M6 simulation, using only an ephemeral identity.

Spawn (never fork) avoids inheriting running services' threads, sockets or
wallet objects. The worker cannot make network connections or submit orders.
"""
import asyncio
import multiprocessing
import os
import socket
import time
from concurrent.futures import ProcessPoolExecutor

from eth_account import Account
from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import ApiCreds

from polymaker.execution.gateway import ExecutionGateway


def virtual_creds():
    return ApiCreds('virtual-only', 'dmlydHVhbA==', 'virtual-only')


def forbidden(*args, **kwargs):
    raise RuntimeError('M6_SIGNER_NETWORK_FORBIDDEN')


class SigningClient(ClobClient):
    _get = _post = _delete = forbidden

    def get_version(self):
        return 2

    def get_tick_size(self, token_id):
        return format(self.ticks[token_id], 'g')


class SigningGateway(ExecutionGateway):
    def __init__(self, key):
        # No HTTP/order pools or submission gateway are created in this worker.
        self._client = SigningClient('https://clob.polymarket.com', 137,
                                     key=key, creds=virtual_creds())

    async def _io(self, fn, *args):
        return fn(*args)


def initialize(key):
    global _worker
    socket.socket.connect = socket.socket.connect_ex = forbidden
    _worker = SigningGateway(key)


def ready():
    # Warm the native Python ECC backend before admission, without creating an
    # order, signal, ledger entry or network request.
    _worker._client.signer.sign('0x'+'00'*32)
    return os.getpid()


def prepare(quotes, meta, ticks):
    began, cpu = time.monotonic(), time.process_time()
    _worker._client.ticks = ticks
    result = asyncio.run(_worker.prepare(quotes, meta, ticks))
    return result, {'worker_pid': os.getpid(), 'began_monotonic': began,
                    'ended_monotonic': time.monotonic(),
                    'cpu_ms': (time.process_time()-cpu)*1000}


class M6Signer:
    def __init__(self):
        self.key = Account.create().key.hex()
        self.pool = ProcessPoolExecutor(max_workers=1,
            mp_context=multiprocessing.get_context('spawn'),
            initializer=initialize, initargs=(self.key,))
        self.worker_pid = None
        self.last_prepare_timing = None

    async def warmup(self):
        self.worker_pid = await asyncio.get_running_loop().run_in_executor(self.pool, ready)

    async def prepare(self, quotes, meta, ticks):
        began = time.monotonic()
        result, worker = await asyncio.get_running_loop().run_in_executor(self.pool, prepare, quotes, meta, ticks)
        ended = time.monotonic()
        self.last_prepare_timing = {**worker, 'duration_ms': (ended-began)*1000,
            'queue_ms': (worker['began_monotonic']-began)*1000,
            'worker_wall_ms': (worker['ended_monotonic']-worker['began_monotonic'])*1000,
            'result_delivery_ms': (ended-worker['ended_monotonic'])*1000}
        return result

    def close(self):
        self.pool.shutdown(wait=True, cancel_futures=True)
