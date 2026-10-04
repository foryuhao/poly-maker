"""Strategy-independent, fail-closed authorization for a live write boundary.

This pure gate does not grant an execution adapter or make network requests.
The caller must separately enforce its strategy readiness and execution guards.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from eth_account import Account
from eth_utils.address import to_checksum_address


@dataclass(frozen=True, slots=True)
class LiveAuthorizationRequest:
    explicit_write_mode: bool
    armed: bool
    live_environment: bool
    private_key: str | None = field(repr=False)
    configured_funder: str | None
    signature_type: int | None
    connected_signer: str | None
    connected_funder: str | None
    authenticated: bool


@dataclass(frozen=True, slots=True)
class LiveExecutionIdentity:
    signer: str
    funder: str
    signature_type: int


@dataclass(frozen=True, slots=True)
class LiveAuthorizationResult:
    authorized: bool
    reason: str
    identity: LiveExecutionIdentity | None = None


def live_authorization_request(
    config: Any, gateway: Any, *, explicit_write_mode: bool, armed: bool,
) -> LiveAuthorizationRequest:
    """Collect already-known runtime evidence; an absent field denies access."""
    secrets = getattr(config, "secrets", None)
    wallet = getattr(config, "wallet", None)
    return LiveAuthorizationRequest(
        explicit_write_mode=explicit_write_mode,
        armed=armed,
        live_environment=getattr(gateway, "_paper", None) is False,
        private_key=getattr(secrets, "pk", None),
        configured_funder=getattr(secrets, "browser_address", None),
        signature_type=getattr(wallet, "signature_type", None),
        connected_signer=getattr(gateway, "address", None),
        connected_funder=getattr(gateway, "funder", None),
        authenticated=bool(getattr(gateway, "creds", None)),
    )


def validate_live_identity(request: LiveAuthorizationRequest) -> LiveAuthorizationResult:
    """Validate already-connected account identity without I/O or secret output."""
    if not isinstance(request.private_key, str) or not request.private_key or (
        not isinstance(request.configured_funder, str) or not request.configured_funder
    ):
        return LiveAuthorizationResult(False, "CREDENTIALS_MISSING")
    if type(request.signature_type) is not int or request.signature_type not in (0, 1, 2, 3):
        return LiveAuthorizationResult(False, "SIGNATURE_TYPE_INVALID")
    if request.authenticated is not True:
        return LiveAuthorizationResult(False, "AUTHENTICATION_MISSING")
    try:
        signer = Account.from_key(request.private_key).address
        funder = to_checksum_address(request.configured_funder)
    except (ValueError, TypeError, AttributeError):
        return LiveAuthorizationResult(False, "CONFIGURED_IDENTITY_INVALID")
    if request.signature_type == 0 and signer != funder:
        return LiveAuthorizationResult(False, "SIGNER_FUNDER_MISMATCH")
    if not isinstance(request.connected_signer, str) or request.connected_signer.lower() != signer.lower():
        return LiveAuthorizationResult(False, "CONNECTED_SIGNER_MISMATCH")
    if not isinstance(request.connected_funder, str) or request.connected_funder.lower() != funder.lower():
        return LiveAuthorizationResult(False, "CONNECTED_FUNDER_MISMATCH")
    return LiveAuthorizationResult(
        True, "AUTHORIZED",
        LiveExecutionIdentity(signer, funder, request.signature_type),
    )


def authorize_live_write(request: LiveAuthorizationRequest) -> LiveAuthorizationResult:
    """Require explicit mode and arm before a real trading write can be authorized."""
    if request.explicit_write_mode is not True:
        return LiveAuthorizationResult(False, "WRITE_MODE_NOT_EXPLICIT")
    if request.armed is not True:
        return LiveAuthorizationResult(False, "NOT_ARMED")
    if request.live_environment is not True:
        return LiveAuthorizationResult(False, "NOT_LIVE_ENVIRONMENT")
    return validate_live_identity(request)
