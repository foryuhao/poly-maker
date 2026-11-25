"""Persistent stop controls. This module never resumes trading or clears a latch."""
import json
import os
import tempfile
import time
import tomllib
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool


class BalanceSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    enabled: StrictBool = True
    threshold_usdc: Decimal = Field(default=Decimal(20), gt=0)


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name+'.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, default=str, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def legacy_entry_guard(project=None):
    """Retire old service entrypoints while keeping other research imports usable."""
    project = Path(project) if project else Path(__file__).resolve().parents[2]
    if (project/'runtime/trading-control/m1-retired.json').exists():
        raise RuntimeError('M1_RUNTIME_RETIRED_USE_M4_AFTER_EXPLICIT_CONFIRMATION')


class StopControl:
    def __init__(self, project, *, live, state_dir=None):
        self.project, self.live = Path(project).resolve(), live
        state = Path(state_dir).resolve() if state_dir is not None else self.project
        self.root = state/'runtime/trading-control'
        self.manual_path = self.root/('m4-live-stop.json' if live else 'm4-baseline-stop.json')
        self.balance_path = self.root/'balance-stop.json'
        self.legacy_path = state/'research_outputs/m1_live_watch/balance-stop.json'
        self.config_path = self.project/'config/m4-live/balance-guard.toml'

    def inhibited(self):
        return (self.manual_path.exists() or self.live and
                (self.balance_path.exists() or self.legacy_path.exists()))

    def observe_balance(self, amount):
        if not self.live:
            return None
        status, error = 'VALID', None
        try:
            raw = tomllib.loads(self.config_path.read_text(), parse_float=Decimal)
            settings = BalanceSettings.model_validate(raw)
        except (ValueError, OSError) as exc:
            settings, status, error = BalanceSettings(), 'INVALID_USING_DEFAULTS', type(exc).__name__
        amount = Decimal(str(amount))
        if not amount.is_finite() or amount < 0:
            raise ValueError('M4_BALANCE_UNAVAILABLE')
        if settings.enabled and amount < settings.threshold_usdc and not self.balance_path.exists():
            atomic_json(self.balance_path, {'id': uuid4().hex, 'created_at': time.time(),
                'reason': 'CASH_BELOW_THRESHOLD', 'balance_usdc': str(amount),
                'threshold_usdc': str(settings.threshold_usdc), 'requires_confirmation': True})
        result = {'observed_at': time.time(), 'balance_usdc': str(amount),
            'enabled': settings.enabled, 'threshold_usdc': str(settings.threshold_usdc),
            'configuration_status': status, 'configuration_error_type': error,
            'requires_confirmation': self.balance_path.exists() or self.legacy_path.exists()}
        atomic_json(self.root/'m4-balance-observation.json', result)
        return result
