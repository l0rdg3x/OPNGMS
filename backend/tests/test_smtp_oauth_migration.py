from sqlalchemy import text


async def test_smtp_settings_has_oauth_columns(db_engine):
    async with db_engine.begin() as conn:
        cols = set((await conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name='smtp_settings'"
        ))).scalars().all())
    assert {"auth_method", "oauth_provider", "oauth_client_id",
            "oauth_client_secret_enc", "oauth_refresh_token_enc", "oauth_tenant_id"} <= cols
