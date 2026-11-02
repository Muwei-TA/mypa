import os
from contextlib import contextmanager
from pathlib import Path

from alembic.config import Config
from sqlalchemy import URL, create_engine, event
from sqlalchemy.engine import Engine

from alembic import command
from pa.common import PAError

SCHEMA_HEAD = "0003_schedules"


def make_engine(path: str | Path) -> Engine:
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    local_flag = getattr(os, "ST_LOCAL", None)
    if local_flag and not os.statvfs(path.parent).f_flag & local_flag:
        raise PAError("SQLite must be stored on a local disk")
    engine = create_engine(
        URL.create("sqlite+pysqlite", database=str(path)),
        connect_args={"check_same_thread": False, "timeout": 5},
    )

    @event.listens_for(engine, "connect")
    def configure(connection, record):
        connection.isolation_level = None
        cursor = connection.cursor()
        for pragma in (
            "journal_mode=WAL",
            "synchronous=NORMAL",
            "busy_timeout=5000",
            "foreign_keys=ON",
        ):
            cursor.execute(f"PRAGMA {pragma}")
        cursor.close()

    @event.listens_for(engine, "begin")
    def begin(connection):
        statement = (
            "BEGIN IMMEDIATE" if connection.get_execution_options().get("pa_writer") else "BEGIN"
        )
        connection.exec_driver_sql(statement)

    return engine


@contextmanager
def transaction(engine: Engine):
    """Take the SQLite writer lock before reads used to decide writes."""
    with engine.connect().execution_options(pa_writer=True) as connection:
        connection.begin()
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise


def migrate(engine: Engine) -> None:
    packaged = Path(__file__).parents[1] / "migrations"
    scripts = packaged if packaged.is_dir() else Path(__file__).parents[3] / "alembic"
    config = Config()
    config.set_main_option("script_location", str(scripts))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
