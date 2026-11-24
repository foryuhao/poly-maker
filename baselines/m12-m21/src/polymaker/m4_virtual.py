"""Real signing and pre-POST checks, with a permanently modeled POST endpoint."""
import time
from dataclasses import asdict
from types import SimpleNamespace

from eth_account import Account
from py_clob_client_v2.clob_types import ApiCreds

from polymaker.config import RDPSMConfig
from polymaker.execution.gateway import ExecutionGateway
from polymaker.m4_fill_model import LEGACY_POLICY, POLICY, TouchFillModel
from polymaker.rdp_sm_execution import ExecutionClobClient


class VirtualClient(ExecutionClobClient):
    def __init__(self, reads, ledger):
        self.reads, self.ledger = reads, ledger
        super().__init__('https://clob.polymarket.com', 137, key=Account.create().key.hex(),
            creds=ApiCreds('virtual-only', 'dmlydHVhbA==', 'virtual-only'))
        self.known_ids, self.observed_open_rows = set(), {}
        self._pagination_failures = {}
        self.sm_config = RDPSMConfig(normal_entry_mode='m4_v2')

    def get_version(self):
        return 2

    def get_tick_size(self, token_id):
        return format(self.reads.feed.books[token_id].tick, 'f')

    def _get(self, *args, **kwargs):
        raise RuntimeError('M4_VIRTUAL_PRIVATE_READ_FORBIDDEN')

    def _delete(self, *args, **kwargs):
        raise RuntimeError('M4_VIRTUAL_DELETE_FORBIDDEN')

    def _native_order_post(self, endpoint, headers=None, data=None, params=None):
        results = []
        for attempt in self._operation_value('batch_attempts'):
            if self.reads.fill_model is None:
                # Existing markets without a model marker keep their original
                # receipt semantics. New model POSTs only acknowledge here.
                self.ledger.fact('virtual_fill', attempt.expected_order_id,
                    {'opportunity_id': attempt.quote.window_id, 'order_id': attempt.expected_order_id,
                     'quote': asdict(attempt.quote), 'wall': time.time(),
                     'model': 'FULL_FILL_AT_INSTRUCTION_PRICE'})
            results.append({'success': True, 'orderID': attempt.expected_order_id})
        return results


class VirtualGateway(ExecutionGateway):
    def __init__(self, reads, ledger):
        super().__init__(SimpleNamespace(wallet=SimpleNamespace(data_api_host='https://data-api.polymarket.com'),
            execution=SimpleNamespace(rate_budget_fraction=1)))
        self._client = VirtualClient(reads, ledger)


class VirtualReads:
    def __init__(self, feed, ledger, api):
        self.feed, self.api = feed, api
        policy = ledger.get('baseline_fill_model')
        if policy is not None and policy not in (POLICY, LEGACY_POLICY):
            raise ValueError('M4_BASELINE_FILL_MODEL_UNSUPPORTED')
        self.fill_model = TouchFillModel(feed, ledger) if policy is not None else None
        feed.fill_model = self.fill_model
        self.gateway = VirtualGateway(self, ledger)
        self.network = SimpleNamespace(
            snapshot=lambda: SimpleNamespace(allowed=feed.connected,
                reason=None if feed.connected else 'WS_DISCONNECTED'),
            recent_writes=lambda: SimpleNamespace(post=SimpleNamespace(max_ms=0)))

    def close(self):
        if self.fill_model is not None:
            self.fill_model.stop('BASELINE_CLOSED_BEFORE_OBSERVATION_END')
        self.gateway.close()
