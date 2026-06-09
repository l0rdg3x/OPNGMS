import json
import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://opngms:opngms@localhost:5432/opngms")
os.environ.setdefault("SESSION_SECRET", "export")
os.environ.setdefault("MASTER_KEY", "export-placeholder")  # non usato da openapi()

from app.main import app  # noqa: E402

print(json.dumps(app.openapi(), indent=2))
