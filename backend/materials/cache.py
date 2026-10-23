import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import time

DB = Path(__file__).resolve().parents[2] / "data" / "materials_cache.sqlite"
TTL = 24 * 3600


@contextmanager
def _connect():
    db = sqlite3.connect(DB, timeout=15)
    db.execute("CREATE TABLE IF NOT EXISTS cache (namespace TEXT, key TEXT, value TEXT, fetched_at TEXT, expires REAL, PRIMARY KEY(namespace,key))")
    try:
        yield db
        db.commit()
    finally:
        db.close()


def get(namespace, key):
    with _connect() as db:
        row = db.execute("SELECT value,fetched_at,expires FROM cache WHERE namespace=? AND key=?", (namespace, key)).fetchone()
    if row and row[2] > time():
        return {"data": json.loads(row[0]), "fetched_at": row[1], "cached": True}
    return None


def put(namespace, key, data, ttl=TTL):
    fetched_at = datetime.now(timezone.utc).isoformat()
    with _connect() as db:
        db.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?,?,?)", (namespace, key, json.dumps(data, ensure_ascii=False), fetched_at, time()+ttl))
    return {"data": data, "fetched_at": fetched_at, "cached": False}


def query_key(**query):
    return json.dumps(query, sort_keys=True, ensure_ascii=False)
