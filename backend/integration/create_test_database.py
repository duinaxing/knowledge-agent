"""Create a dedicated test database. Refuses to drop or replace an existing database."""
import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url
from knowledge_agent.config import settings

name='knowledge_test_project2_20260912'
url=make_url(settings.database_url)
with psycopg.connect(host=url.host,port=url.port or 5432,user=url.username,password=url.password,
                     dbname='postgres',autocommit=True,connect_timeout=5) as conn:
    exists=conn.execute('SELECT 1 FROM pg_database WHERE datname=%s',(name,)).fetchone()
    if not exists:
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
print('Dedicated test database ready: '+name)
