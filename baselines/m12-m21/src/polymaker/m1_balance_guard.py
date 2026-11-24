"""Five-minute cash circuit breaker; no trading or position client exists here."""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import tempfile
import time
import tomllib
import uuid
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, StrictBool

THRESHOLD = Decimal('20')
LIVE_SERVICE = 'polymaker-m1-live.service'
CONFIG_PATH = Path('config/m1-live/balance-guard.toml')


class BalanceGuardSettings(BaseModel):
    """Monitor-only settings; never part of live/virtual session identity."""

    model_config = ConfigDict(extra='forbid')
    enabled: StrictBool = True
    threshold_usdc: Decimal = Field(default=THRESHOLD, gt=0, allow_inf_nan=False)

    @classmethod
    def load(cls, path):
        try:
            content = path.read_text()
        except FileNotFoundError:
            return cls()
        return cls.model_validate(tomllib.loads(content, parse_float=Decimal))


def atomic_json(path, value):
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.'+path.name+'-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
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


def live_wallet(project, markets, previous):
    wallets = set()
    for market in markets:
        if not market.get('journal_path'):
            continue
        path = Path(market['journal_path']).resolve()
        if path.parent != (project/'journal').resolve():
            raise ValueError('BALANCE_JOURNAL_OUTSIDE_PROJECT')
        match = re.fullmatch(r'rdp_sm_(0x[0-9a-f]{40})_(m1_live_[0-9a-f]{32})\.jsonl', path.name)
        with path.open() as stream:
            first = json.loads(stream.readline())
        if (not match or first['kind'] != 'sm_session'
                or first['data']['session_id'] != match[2]
                or first['data']['payload'].get('execution_mode') != 'LIVE'):
            raise ValueError('BALANCE_LIVE_IDENTITY_UNAVAILABLE')
        wallets.add(match[1])
    if not wallets and previous.get('wallet'):
        wallets.add(previous['wallet'])
    if len(wallets) != 1:
        raise ValueError('BALANCE_LIVE_IDENTITY_UNAVAILABLE')
    wallet = wallets.pop()
    if not re.fullmatch(r'0x[0-9a-f]{40}', wallet):
        raise ValueError('BALANCE_LIVE_IDENTITY_INVALID')
    return wallet


def check_balance(project, output, markets, *, fetch=None, runner=subprocess.run, clock=time.time):
    # balance.fetch_balance enforces HTTPS and two GET routes in its transport:
    # existing-key derivation and collateral balance only, never key creation.
    if fetch is None:
        from polymaker.dashboard.balance import fetch_balance

        fetch = fetch_balance
    now = clock()
    path, latch_path = output/'balance-guard.json', output/'balance-stop.json'
    try:
        previous = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(previous, dict):
            previous = {}
    except (OSError, ValueError):
        previous = {}
    latch = None
    if latch_path.exists():
        try:
            latch = json.loads(latch_path.read_text())
            if not isinstance(latch, dict) or not latch.get('id') or 'balance_usdc' not in latch:
                raise ValueError('BALANCE_STOP_MARKER_INVALID')
        except (OSError, ValueError):
            # Existence is authoritative, just as in the service condition.
            # Corrupt stop evidence must never unlock trading or invent cash.
            latch = {'id': 'UNREADABLE_STOP_MARKER', 'balance_usdc': None}
    config_path = project/CONFIG_PATH
    config_status, config_error = 'VALID', None
    try:
        settings = BalanceGuardSettings.load(config_path)
    except (OSError, ValueError) as exc:
        # A partial/malformed edit cannot silently disable protection. The
        # durable latch is handled independently, even if configuration fails.
        settings = BalanceGuardSettings()
        config_status, config_error = 'INVALID_USING_DEFAULTS', type(exc).__name__
    threshold = settings.threshold_usdc
    result = {'checked_at': now, 'enabled': settings.enabled,
              'threshold_usdc': str(threshold), 'config_path': str(config_path),
              'configuration_status': config_status,
              'status': 'BALANCE_UNAVAILABLE', 'requires_confirmation': latch is not None}
    if config_error is not None:
        result['config_error_type'] = config_error
    try:
        wallet = live_wallet(project, markets, previous)
        result['wallet'] = wallet
        observation = fetch(project, wallet)
        amount = Decimal(observation['balance_usdc'])
        observed_at = observation['observed_at']
        if (observation.get('state') != 'AVAILABLE' or observation.get('wallet') != wallet
                or not amount.is_finite() or amount < 0
                or not isinstance(observed_at, (int, float)) or isinstance(observed_at, bool)
                or not math.isfinite(observed_at) or not -5 <= clock()-observed_at <= 30):
            raise ValueError('BALANCE_RESPONSE_INVALID_OR_STALE')
        result['observation'] = {k: observation[k] for k in
                                 ('state', 'wallet', 'balance_usdc', 'observed_at', 'source')}
        result['status'] = 'HEALTHY' if settings.enabled else 'DISABLED'
        if settings.enabled and amount < threshold and latch is None:
            latch = {'id': uuid.uuid4().hex, 'created_at': clock(), 'wallet': wallet,
                     'balance_usdc': str(amount), 'threshold_usdc': str(threshold),
                     'reason': f'M1_BALANCE_BELOW_{threshold}', 'requires_confirmation': True}
            # Durable inhibit precedes SIGTERM. Service startup has a matching
            # ConditionPathExists check, so restart cannot discard this latch.
            atomic_json(latch_path, latch)
    except Exception as exc:
        # Never serialize exception messages or authenticated HTTP bodies.
        result['read_error_type'] = type(exc).__name__
    if latch is not None:
        result.update(status='AWAITING_CONFIRMATION', requires_confirmation=True,
                      stop_event_id=latch['id'], triggered_balance_usdc=latch['balance_usdc'])
        try:
            stopped = runner(['systemctl', '--user', 'stop', '--no-block', LIVE_SERVICE],
                             capture_output=True, text=True, timeout=10, check=False)
            result['stop_requested'] = stopped.returncode == 0
            if stopped.returncode:
                result['stop_returncode'] = stopped.returncode
        except (OSError, subprocess.SubprocessError) as exc:
            result.update(stop_requested=False, stop_error_type=type(exc).__name__)
    atomic_json(path, result)
    return result
