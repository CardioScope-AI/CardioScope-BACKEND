"""Use the same URL builder for migrations and application connections."""
from alembic import context
from app.database import database_url, get_engine
from app.models import Base


if context.is_offline_mode():
    context.configure(url=database_url(), target_metadata=Base.metadata,
                      literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = get_engine()
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=Base.metadata,
                              compare_type=True, compare_server_default=True)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()
