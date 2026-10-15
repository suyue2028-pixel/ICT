from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column
from backend.db.base import Base, IdentityMixin, TimestampMixin


class UserAccount(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "user_accounts"
    phone: Mapped[str] = mapped_column(String(16), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    phone_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
