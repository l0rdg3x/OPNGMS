from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin


class Tenant(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "tenants"

    name: Mapped[str]
    slug: Mapped[str] = mapped_column(unique=True)
    status: Mapped[str] = mapped_column(default="active")
    note: Mapped[str | None] = mapped_column(default=None)
