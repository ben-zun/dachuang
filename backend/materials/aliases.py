import sqlite3
from contextlib import contextmanager
import unicodedata
import re
from pathlib import Path

from rapidfuzz import process, fuzz

DB = Path(__file__).resolve().parents[2] / "data" / "material_aliases.sqlite"
PRIORITY = {"manual": 100, "wikidata": 80, "pubchem": 70}


def normalize(value):
    return " ".join(unicodedata.normalize("NFKC", value).strip().split()).casefold()


@contextmanager
def connect():
    db = sqlite3.connect(DB, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE IF NOT EXISTS aliases (alias TEXT, normalized_alias TEXT, formula TEXT, language TEXT, source TEXT, priority INTEGER, source_url TEXT, verified_at TEXT, PRIMARY KEY(normalized_alias,formula,source))")
    try:
        yield db
        db.commit()
    finally:
        db.close()


def add(alias, formula, language, source, source_url="", verified_at=""):
    with connect() as db:
        db.execute("INSERT OR REPLACE INTO aliases VALUES (?,?,?,?,?,?,?,?)",
                   (alias, normalize(alias), formula, language, source, PRIORITY[source], source_url, verified_at))


def lookup(query):
    with connect() as db:
        rows = db.execute("SELECT * FROM aliases WHERE normalized_alias=? ORDER BY priority DESC", (normalize(query),)).fetchall()
    if not rows:
        return []
    # 高优先级人工歧义也保留多个候选，不能被低优先级来源覆盖。
    highest = rows[0]["priority"]
    unique = {}
    for row in rows:
        if row["priority"] == highest:
            unique.setdefault(row["formula"], dict(row))
    return list(unique.values())


def fuzzy(query):
    normalized = normalize(query)
    with connect() as db:
        names = [row[0] for row in db.execute("SELECT DISTINCT normalized_alias FROM aliases")]
    matches = process.extract(normalized, names, scorer=fuzz.ratio, score_cutoff=70, limit=8)
    candidates = {}
    for name, score, _ in matches:
        for row in lookup(name):
            if row['formula'] not in candidates or score>candidates[row['formula']]['score']:
                candidates[row['formula']]={**row,'score':round(score,1)}
    return sorted(candidates.values(),key=lambda r:-r['score'])[:5]


def names(formula):
    with connect() as db:
        rows = db.execute("SELECT alias,language FROM aliases WHERE formula=? ORDER BY priority DESC, length(alias)", (formula,)).fetchall()
    result = {"zh": None, "en": None}
    for row in rows:
        if len(lookup(row["alias"])) > 1:
            continue
        if row["language"] == "en" and (len(row["alias"]) <= 3 or re.fullmatch(r"(?:[A-Z][a-z]?\d*)+", unicodedata.normalize("NFKC", row["alias"]))):
            continue
        if row["language"] in result and result[row["language"]] is None:
            result[row["language"]] = row["alias"]
    return result
