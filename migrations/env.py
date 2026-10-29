from alembic import context

from app.core.config import Settings
from app.database import make_engine
from app.models import metadata

config = context.config


def run_migrations():
    # Tests may supply a connection, avoiding credentials in config/log files.
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(connection=connection, target_metadata=metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    settings = Settings()
    if settings.database_url is None:
        raise RuntimeError("请设置 JOB_AGENT_DATABASE_URL 后执行迁移")
    url = settings.database_url.get_secret_value()
    if context.is_offline_mode():
        context.configure(url=url, target_metadata=metadata, literal_binds=True)
        with context.begin_transaction():
            context.run_migrations()
    else:
        engine = make_engine(url)
        try:
            with engine.connect() as connection:
                context.configure(
                    connection=connection, target_metadata=metadata, compare_type=True
                )
                with context.begin_transaction():
                    context.run_migrations()
        finally:
            engine.dispose()


run_migrations()
