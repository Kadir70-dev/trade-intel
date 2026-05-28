"""SQLAlchemy declarative Base.

Kept isolated from session.py so models can import `Base` without triggering
engine construction or settings load. All ORM models inherit from `Base`.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Project-wide ORM declarative base."""
