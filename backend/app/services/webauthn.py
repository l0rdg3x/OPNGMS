"""Thin wrappers over py_webauthn for the registration + authentication ceremonies. No
private/secret material is handled here (WebAuthn is public-key); challenges + credential ids are
base64url. Raises WebAuthnError on any verification mismatch."""
from __future__ import annotations

import uuid

import webauthn
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.models.webauthn_credential import WebAuthnCredential


class WebAuthnError(Exception):
    """A WebAuthn ceremony failed verification. Safe to surface; carries no key material."""


def registration_options(*, user_id: bytes, user_name: str, rp_id: str, rp_name: str,
                         existing_cred_ids: list[bytes]) -> tuple[str, str]:
    opts = webauthn.generate_registration_options(
        rp_id=rp_id, rp_name=rp_name, user_id=user_id, user_name=user_name,
        exclude_credentials=[PublicKeyCredentialDescriptor(id=c) for c in existing_cred_ids],
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.DISCOURAGED,
            user_verification=UserVerificationRequirement.PREFERRED),
    )
    return webauthn.options_to_json(opts), bytes_to_base64url(opts.challenge)


def _verify_reg_raw(*, response: dict, challenge: str, rp_id: str, origin: str):
    # Indirection for tests: holds the raw py_webauthn call (incl. the base64url decode of the
    # challenge) so a monkeypatch can replace it without needing a real authenticator response.
    return webauthn.verify_registration_response(
        credential=response, expected_challenge=_b64(challenge),
        expected_rp_id=rp_id, expected_origin=origin)


def verify_registration(*, response: dict, challenge: str, rp_id: str, origin: str):
    try:
        v = _verify_reg_raw(response=response, challenge=challenge, rp_id=rp_id, origin=origin)
    except Exception as exc:  # py_webauthn raises various verification errors
        raise WebAuthnError("registration verification failed") from exc
    return v  # has .credential_id, .credential_public_key, .sign_count, .aaguid


def authentication_options(*, rp_id: str, allow_cred_ids: list[bytes]) -> tuple[str, str]:
    opts = webauthn.generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=[PublicKeyCredentialDescriptor(id=c) for c in allow_cred_ids],
        user_verification=UserVerificationRequirement.PREFERRED)
    return webauthn.options_to_json(opts), bytes_to_base64url(opts.challenge)


def _verify_auth_raw(*, response: dict, challenge: str, rp_id: str, origin: str,
                     public_key: bytes, sign_count: int):
    # Indirection for tests: holds the raw py_webauthn call (incl. the base64url decode of the
    # challenge) so a monkeypatch can drive the sign-count logic without a real authenticator.
    return webauthn.verify_authentication_response(
        credential=response, expected_challenge=_b64(challenge),
        expected_rp_id=rp_id, expected_origin=origin,
        credential_public_key=public_key, credential_current_sign_count=sign_count)


def verify_authentication(*, response: dict, challenge: str, rp_id: str, origin: str,
                         public_key: bytes, sign_count: int) -> int:
    try:
        v = _verify_auth_raw(response=response, challenge=challenge, rp_id=rp_id, origin=origin,
                            public_key=public_key, sign_count=sign_count)
    except Exception as exc:
        raise WebAuthnError("authentication verification failed") from exc
    # Anti-cloned-authenticator: many authenticators keep a monotonic counter; reject a non-increase
    # (unless the authenticator reports 0/0, the documented "no counter" case).
    if not (v.new_sign_count > sign_count or (v.new_sign_count == 0 and sign_count == 0)):
        raise WebAuthnError("sign count did not increase")
    return v.new_sign_count


def _b64(value: str) -> bytes:
    return base64url_to_bytes(value)


async def has_webauthn_credentials(session: AsyncSession, user_id: uuid.UUID) -> bool:
    """True if the user has at least one registered passkey (a cheap EXISTS, no rows loaded)."""
    return bool(
        await session.scalar(
            select(exists().where(WebAuthnCredential.user_id == user_id))
        )
    )
