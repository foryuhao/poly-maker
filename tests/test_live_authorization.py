"""The generic gate is pure; these fakes never connect to a trading client."""

from dataclasses import replace

import pytest
from eth_account import Account

from polymaker.live_authorization import (
    LiveAuthorizationRequest,
    authorize_live_write,
    validate_live_identity,
)

TEST_KEY = "0x" + "1" * 64
TEST_SIGNER = Account.from_key(TEST_KEY).address
READY = LiveAuthorizationRequest(
    explicit_write_mode=True,
    armed=True,
    live_environment=True,
    private_key=TEST_KEY,
    configured_funder=TEST_SIGNER,
    signature_type=0,
    connected_signer=TEST_SIGNER,
    connected_funder=TEST_SIGNER,
    authenticated=True,
)


def test_valid_generic_authorization_without_strategy_state():
    result = authorize_live_write(READY)
    assert result.authorized
    assert result.identity is not None
    assert (result.identity.signer, result.identity.funder,
            result.identity.signature_type) == (TEST_SIGNER, TEST_SIGNER, 0)
    assert validate_live_identity(READY).authorized
    assert TEST_KEY not in repr(READY)


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"explicit_write_mode": False}, "WRITE_MODE_NOT_EXPLICIT"),
        ({"armed": False}, "NOT_ARMED"),
        ({"live_environment": False}, "NOT_LIVE_ENVIRONMENT"),
        ({"private_key": None}, "CREDENTIALS_MISSING"),
        ({"authenticated": False}, "AUTHENTICATION_MISSING"),
        ({"configured_funder": "0x" + "2" * 40}, "SIGNER_FUNDER_MISMATCH"),
        ({"connected_signer": "0x" + "2" * 40}, "CONNECTED_SIGNER_MISMATCH"),
        ({"connected_funder": "0x" + "2" * 40}, "CONNECTED_FUNDER_MISMATCH"),
        ({"signature_type": 7}, "SIGNATURE_TYPE_INVALID"),
        ({"configured_funder": "invalid"}, "CONFIGURED_IDENTITY_INVALID"),
    ],
)
def test_generic_gate_denies_unverified_authority(change, reason):
    result = authorize_live_write(replace(READY, **change))
    assert not result.authorized
    assert result.identity is None
    assert result.reason == reason


def test_proxy_signature_mode_preserves_distinct_signer_and_funder():
    proxy = replace(READY, signature_type=2,
                    configured_funder="0x" + "2" * 40,
                    connected_funder="0x" + "2" * 40)
    assert authorize_live_write(proxy).authorized
