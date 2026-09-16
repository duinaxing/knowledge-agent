"""Explicit setup operations: migrations, seed, indexing and synthetic publication."""
import argparse
from datetime import datetime, timezone
from sqlalchemy import select
from .config import ROOT
from .db import transaction, Version, Document, Job, initialize_test_database
from .seed import seed
from .worker import work_one
from .documents import publish
from .schemas import Publish


def main():
    parser=argparse.ArgumentParser()
    for name in ('migrate','seed','index','publish-synthetic','sqlite-demo'):
        parser.add_argument('--'+name,action='store_true')
    args=parser.parse_args()
    if args.sqlite_demo:
        initialize_test_database()
    if args.migrate:
        from alembic.config import Config
        from alembic import command
        command.upgrade(Config(str(ROOT/'backend/alembic.ini')),'head')
    if args.seed:seed()
    if args.index:
        while work_one(Job):pass
    if args.publish_synthetic:
        with transaction() as db:
            versions=list(db.scalars(select(Version).where(Version.processing=='ready',Version.publication=='draft')))
            for v in versions:
                if not v.document_id.startswith('d') or not v.document_id[1:].isdigit():
                    continue
                doc=db.get(Document,v.document_id)
                publish(db,v.id,Publish(revision=doc.revision,effective_from=datetime(2026,9,1,tzinfo=timezone.utc)))
    print('Setup operations completed. Inspect processing status before publication.')


if __name__=='__main__':main()
