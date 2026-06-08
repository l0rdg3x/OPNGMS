from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin


class User(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(unique=True)
    name: Mapped[str]
    password_hash: Mapped[str]
    is_superadmin: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(default="active")
    last_login: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
