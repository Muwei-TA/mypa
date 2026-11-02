from alembic import context
from pa.store.db import make_engine


def run():
    config = context.config
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
        return
    path = config.get_main_option("pa_database")
    if not path:
        raise RuntimeError("Use pa migrate --config-dir DIR, or set pa_database in alembic.ini")
    engine = make_engine(path)
    try:
        with engine.begin() as conn:
            context.configure(connection=conn)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


run()
