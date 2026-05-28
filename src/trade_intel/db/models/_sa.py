"""Internal SQLAlchemy column helpers shared across model files."""

from enum import Enum as _PyEnum

from sqlalchemy import Enum as _SAEnum


def str_enum_type(enum_cls: type[_PyEnum], length: int) -> _SAEnum:
    """SA `Enum` column that persists `member.value` as TEXT.

    Defaults applied:
      - `native_enum=False`   — emit a CHECK constraint, identical on SQLite & PG.
      - `validate_strings=True` — reject unknown strings at insert / load.
      - `values_callable`     — store `.value` (e.g. "whatsapp"), not `.name`
                                 ("WHATSAPP"). Schema values are lowercase.
    """
    return _SAEnum(
        enum_cls,
        native_enum=False,
        length=length,
        validate_strings=True,
        values_callable=lambda members: [e.value for e in members],
    )
