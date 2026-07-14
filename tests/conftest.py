import os

# Must run before any codecartographer import: Settings() reads this env var once,
# at import time, so setting it later would silently point tests at the dev DB.
os.environ["CODECART_DATABASE_URL"] = os.environ.get(
    "CODECART_TEST_DATABASE_URL", "postgresql+psycopg://codecart:codecart@localhost:5432/codecart"
)

import pytest
from sqlalchemy.orm import Session

from codecartographer.db.models import Base
from codecartographer.db.session import get_engine, get_session_factory


@pytest.fixture(scope="session", autouse=True)
def _schema():
    engine = get_engine()
    Base.metadata.create_all(engine)
    yield


@pytest.fixture()
def db_session() -> Session:
    session = get_session_factory()()
    engine = get_engine()
    yield session
    session.rollback()
    session.close()
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
