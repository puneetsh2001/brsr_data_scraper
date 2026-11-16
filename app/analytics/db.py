"""Read-only database access for the analytics API.

Every connection is opened through app.db.connection with write=False, so
PostgreSQL itself refuses writes (read-only session). The production
database is refused as an analytics source until a production import has
been approved; the default is the Phase 14 test database.
"""

from contextlib import contextmanager

from psycopg2.extras import RealDictCursor

from app.config import settings
from app.db import connection as dbc


class AnalyticsDatabaseRefused(RuntimeError):
    pass


def analytics_database(name=None):
    name = name or settings.analytics_db_name
    if not name:
        raise AnalyticsDatabaseRefused("no analytics database configured")
    if name == dbc.production_database():
        raise AnalyticsDatabaseRefused(
            f"'{name}' is the production database; the analytics API reads "
            "only a test database until a production import is approved")
    return name


@contextmanager
def cursor(dbname=None):
    conn = dbc.connect(analytics_database(dbname), write=False)
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        yield cur
    finally:
        conn.close()
