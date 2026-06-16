from app.services.webauthn_settings import WebAuthnConfig, get_webauthn_config


async def test_unconfigured_is_not_usable(db_session):
    cfg = await get_webauthn_config(db_session)
    assert isinstance(cfg, WebAuthnConfig)
    assert cfg.is_configured() is (bool(cfg.rp_id) and bool(cfg.origin))


async def test_db_override_makes_it_configured(db_session):
    from app.services.app_settings import set_webauthn_settings

    await set_webauthn_settings(
        db_session, rp_id="opngms.test", rp_name="OPNGMS", origin="https://opngms.test")
    await db_session.commit()
    cfg = await get_webauthn_config(db_session)
    assert cfg.rp_id == "opngms.test"
    assert cfg.rp_name == "OPNGMS"
    assert cfg.origin == "https://opngms.test"
    assert cfg.is_configured() is True
