"""Anonymous browser-scoped chat storage; SQLite contains no API keys."""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time


def database_path():
    return Path(os.getenv('CHAT_HISTORY_DB', str(Path(__file__).resolve().parents[1] / 'data/chat_history/history.sqlite')))


def owner_key(token):
    if not isinstance(token, str) or not re.fullmatch(r'[a-f0-9]{64}', token):
        raise ValueError('Invalid browser identity')
    return hashlib.sha256(token.encode()).hexdigest()


def connect():
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(path, timeout=15)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS chats (owner TEXT, id TEXT, updated REAL, payload TEXT, PRIMARY KEY(owner,id))')
    try:
        path.chmod(0o600)
    except PermissionError:
        pass
    return db


def load(token):
    with connect() as db:
        rows = db.execute('SELECT id,payload FROM chats WHERE owner=? ORDER BY updated', (owner_key(token),)).fetchall()
    return {ident: json.loads(payload) for ident, payload in rows}


def save(token, ident, chat):
    if not re.fullmatch('[a-f0-9]{32}', ident):
        raise ValueError('Invalid chat id')
    with connect() as db:
        db.execute('INSERT OR REPLACE INTO chats VALUES (?,?,?,?)',
                   (owner_key(token), ident, time.time(), json.dumps(chat, ensure_ascii=False)))


def delete(token, ident):
    if not re.fullmatch('[a-f0-9]{32}', ident):
        raise ValueError('Invalid chat id')
    with connect() as db:
        db.execute('DELETE FROM chats WHERE owner=? AND id=?', (owner_key(token), ident))
