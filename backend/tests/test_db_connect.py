import os

import pytest
from sqlalchemy import text

from app.core.db import make_engine


@pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL non impostata"
)
async def test_engine_can_select_one():
    engine = make_engine(os.environ["TEST_DATABASE_URL"])
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT 1"))
        assert result.scalar_one() == 1
    await engine.dispose()
