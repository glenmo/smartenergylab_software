"""
ORM models for the smartenergylab portal.

Two tables: User (login) and PasswordResetToken (forgot-password flow).
Both small; SQLite is the right size of database here.
"""

from datetime import datetime, timedelta, timezone

from flask_login import UserMixin
from sqlalchemy import String, Integer, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from werkzeug.security import generate_password_hash, check_password_hash

from extensions import db


def _utcnow():
    return datetime.now(timezone.utc)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id: Mapped[int]            = mapped_column(Integer, primary_key=True)
    email: Mapped[str]         = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    last_login: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)

    reset_tokens: Mapped[list["PasswordResetToken"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    # -- helpers -----------------------------------------------------------
    def set_password(self, pw: str):
        # pbkdf2-sha256 via werkzeug. Not bcrypt-strong but parity-good for
        # this threat model (single Flask process, hashes never leave the
        # box) and no native dep on burgan.
        self.password_hash = generate_password_hash(pw, method="pbkdf2:sha256:600000")

    def check_password(self, pw: str) -> bool:
        return check_password_hash(self.password_hash, pw)

    def __repr__(self):
        return f"<User {self.email}>"


class PasswordResetToken(db.Model):
    __tablename__ = "password_reset_tokens"

    id: Mapped[int]      = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    token: Mapped[str]   = mapped_column(Text, unique=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime]    = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="reset_tokens")

    def is_valid(self) -> bool:
        if self.used_at is not None:
            return False
        return _utcnow() < self.expires_at

    @classmethod
    def new_for_user(cls, user: User, token: str, ttl_seconds: int):
        return cls(
            user_id=user.id,
            token=token,
            expires_at=_utcnow() + timedelta(seconds=ttl_seconds),
        )


class LoginEvent(db.Model):
    """
    One row per login POST. Both successes and failures are recorded
    so we can investigate brute-force attempts after the fact and
    trigger alerts on suspicious bursts.

    email_attempted stores whatever the user typed (lowercased) — that
    may not match a real user, in which case user_id stays NULL.
    """
    __tablename__ = "login_events"

    id: Mapped[int]               = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime]          = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False, index=True)
    email_attempted: Mapped[str]  = mapped_column(String(255), nullable=False, index=True)
    success: Mapped[bool]         = mapped_column(Boolean, nullable=False, index=True)
    ip_addr: Mapped[str | None]   = mapped_column(String(64), nullable=True, index=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_id: Mapped[int | None]   = mapped_column(ForeignKey("users.id"), nullable=True)

    user: Mapped["User | None"] = relationship()
