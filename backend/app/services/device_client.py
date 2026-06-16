from app.connectors.opnsense.client import OpnsenseClient
from app.core import crypto
from app.models.device import Device


def client_for_device(device: Device) -> OpnsenseClient:
    """Build an SSRF-guarded OpnsenseClient from a persisted device row.

    Decrypts the Fernet-encrypted API creds and applies the device's TLS settings plus its detected
    edition/version, so the version-aware capability matrix resolves to the right endpoints. The
    per-request timeout comes from OPNSENSE_HTTP_TIMEOUT inside the client. Secrets are decrypted only
    in memory here — never logged or returned.
    """
    return OpnsenseClient(
        device.base_url,
        crypto.decrypt(device.api_key_enc),
        crypto.decrypt(device.api_secret_enc),
        verify_tls=device.verify_tls,
        tls_fingerprint=device.tls_fingerprint,
        edition=device.edition,
        # Full detected version (e.g. "26.1.10"), not the YY.M series — this preserves the exact prior
        # behavior of the one site that already passed a version (the catalog router) and is strictly more
        # precise; the resolver parses point/hotfix too. None (not-yet-probed) -> "" -> newest.
        version=device.firmware_version or "",
    )
