from sqlalchemy import text


async def test_webauthn_schema(db_engine):
    async with db_engine.begin() as conn:
        cred = set((await conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name='webauthn_credential'"
        ))).scalars().all())
        sess = set((await conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name='sessions'"
        ))).scalars().all())
    assert {"id", "user_id", "credential_id", "public_key", "sign_count", "transports",
            "name", "aaguid", "created_at", "last_used_at"} <= cred
    assert "webauthn_challenge" in sess
