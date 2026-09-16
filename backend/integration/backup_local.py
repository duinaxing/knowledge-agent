"""Create a local PostgreSQL custom-format backup without printing credentials."""
import os
import subprocess
from datetime import datetime
from pathlib import Path
from sqlalchemy.engine import make_url
from knowledge_agent.config import settings, ROOT

url=make_url(settings.database_url)
if url.host!='127.0.0.1' or url.database!='knowledge':
    raise SystemExit('This backup helper is scoped to the local demonstration database.')
target=ROOT/'runtime/backups'/('knowledge-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.dump')
target.parent.mkdir(parents=True,exist_ok=True)
binary=ROOT/'runtime/postgres/Library/bin/pg_dump.exe'
env=dict(os.environ,PGPASSWORD=url.password or '')
subprocess.run([str(binary),'-h',url.host,'-p',str(url.port or 5432),'-U',url.username,
                '-d',url.database,'-Fc','-f',str(target)],env=env,check=True,capture_output=True)
print('Backup created:',target.name,target.stat().st_size,'bytes')
