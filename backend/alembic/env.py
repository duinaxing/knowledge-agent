from alembic import context
from sqlalchemy import create_engine
from knowledge_agent.db import Base
from knowledge_agent.config import settings

with create_engine(settings.database_url).connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()
