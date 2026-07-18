"""Create all tables from the ORM metadata.

Convenience bootstrap for dev/test and quick starts. For production schema
management prefer Alembic migrations (``alembic upgrade head``).
"""

from __future__ import annotations

from gridiron.db.models import Base
from gridiron.db.session import get_engine


def create_all() -> None:
    Base.metadata.create_all(bind=get_engine())


def main() -> None:
    create_all()
    print("Created all tables at", get_engine().url)


if __name__ == "__main__":
    main()
