"""Durable, atomic admission counters. Restarting an experiment never resets quota."""
import sqlite3


def reserve(path, kind, cap, request_id):
    if kind not in ('question', 'model') or cap < 1:
        raise ValueError('Invalid budget')
    with sqlite3.connect(path, timeout=15) as db:
        db.execute('CREATE TABLE IF NOT EXISTS admissions(kind TEXT, request_id TEXT, PRIMARY KEY(kind,request_id))')
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM admissions WHERE kind=? AND request_id=?', (kind, request_id)).fetchone():
            return True
        if db.execute('SELECT count(*) FROM admissions WHERE kind=?', (kind,)).fetchone()[0] >= cap:
            return False
        db.execute('INSERT INTO admissions VALUES (?,?)', (kind, request_id))
        return True
