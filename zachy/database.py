from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from zachy.config import settings

# The API, the sync button and the FIT backfill can write at the same time: wait for locks
# instead of failing, and use WAL so readers aren't blocked by a long write.
_sqlite = settings.database_url.startswith("sqlite")
engine = create_engine(settings.database_url, connect_args={"timeout": 30} if _sqlite else {})

if _sqlite:
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()
SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


def add_missing_columns() -> None:
    """create_all() makes new tables but never changes existing ones: add any nullable column a
    model has gained since its table was created (no migrations needed for that common case)."""
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for col in table.columns:
                if col.name not in existing and col.nullable:
                    conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} '
                                      f'{col.type.compile(engine.dialect)}'))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()