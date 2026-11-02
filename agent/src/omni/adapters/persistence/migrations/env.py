"""Alembic environment uses the connection and exclusive ownership of its caller."""

from alembic import context

connection = context.config.attributes["connection"]
context.configure(connection=connection, transactional_ddl=True)
with context.begin_transaction():
    context.run_migrations()
