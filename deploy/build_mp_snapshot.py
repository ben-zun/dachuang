"""Build an indexed SQLite snapshot from the official summary Parquet file."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

import pyarrow.parquet as pq
from pymatgen.core import Composition

COLUMNS = ['material_id', 'formula_pretty', 'chemsys', 'elements', 'nelements',
           'composition_reduced', 'band_gap', 'formation_energy_per_atom',
           'energy_above_hull', 'is_stable', 'density', 'volume', 'is_metal',
           'nsites', 'symmetry', 'structure.lattice', 'deprecated', 'last_updated',
           'builder_meta', 'origins', 'energy_per_atom', 'uncorrected_energy_per_atom',
           'is_magnetic', 'ordering', 'total_magnetization', 'is_gap_direct', 'warnings']

SCHEMA = '''
CREATE TABLE materials (
 material_id TEXT PRIMARY KEY, formula TEXT NOT NULL, formula_key TEXT NOT NULL,
 chemsys TEXT, nelements INTEGER, band_gap REAL, formation_energy_per_atom REAL,
 energy_above_hull REAL, density REAL, volume REAL, space_group TEXT,
 space_group_number INTEGER, crystal_system TEXT, is_metal INTEGER,
 is_stable INTEGER, number_of_sites INTEGER, lattice_parameters TEXT,
 deprecated INTEGER NOT NULL, composition TEXT, last_updated TEXT,
 builder_meta TEXT, origins TEXT, energy_per_atom REAL,
 uncorrected_energy_per_atom REAL, is_magnetic INTEGER, magnetic_ordering TEXT,
 total_magnetization REAL, is_gap_direct INTEGER, warnings TEXT
);
CREATE TABLE material_elements(material_id TEXT, element TEXT,
 PRIMARY KEY(material_id,element));
CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
'''


def encode(value):
    return json.dumps(value, ensure_ascii=False, default=str) if value is not None else None


def build(source, output, version, source_url):
    if output.exists():
        raise ValueError('Refusing to overwrite a snapshot; build a new path first')
    pf = pq.ParquetFile(source)
    schema = set(pf.schema_arrow.names)
    missing = set(COLUMNS) - {'structure.lattice'} - schema
    if missing or 'structure' not in schema:
        raise ValueError('Missing source fields: ' + str(missing))
    output.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(output)
    db.executescript(SCHEMA)
    db.execute('PRAGMA cache_size=-8192')
    db.execute('PRAGMA temp_store=FILE')
    total = active = 0
    licenses = Counter(); batches = Counter(); formula_keys = {}
    sample = []
    for batch in pf.iter_batches(batch_size=2048, columns=COLUMNS, use_threads=False):
        values = []; elements = []
        for r in batch.to_pylist():
            ident = r['material_id']; formula = r['formula_pretty']
            if not ident or not formula:
                raise ValueError('Snapshot record lacks material identity')
            if formula not in formula_keys:
                formula_keys[formula] = Composition(formula).reduced_formula
            sym = r.get('symmetry') or {}
            lattice = (r.get('structure') or {}).get('lattice') or {}
            params = {k: lattice[k] for k in ['a', 'b', 'c', 'alpha', 'beta', 'gamma'] if lattice.get(k) is not None}
            meta = r.get('builder_meta') or {}
            licenses[str(meta.get('license'))] += 1
            batches[str(meta.get('batch_id'))] += 1
            deprecated = int(bool(r.get('deprecated')))
            values.append((ident, formula, formula_keys[formula], r.get('chemsys'), r.get('nelements'),
                r.get('band_gap'), r.get('formation_energy_per_atom'), r.get('energy_above_hull'),
                r.get('density'), r.get('volume'), sym.get('symbol'), sym.get('number'), sym.get('crystal_system'),
                r.get('is_metal'), r.get('is_stable'), r.get('nsites'), encode(params) if params else None,
                deprecated, encode(r.get('composition_reduced')), str(r.get('last_updated') or ''),
                encode(meta), encode(r.get('origins')), r.get('energy_per_atom'),
                r.get('uncorrected_energy_per_atom'), r.get('is_magnetic'), r.get('ordering'),
                r.get('total_magnetization'), r.get('is_gap_direct'), encode(r.get('warnings'))))
            elements.extend((ident, e) for e in r.get('elements') or [])
            active += not deprecated
            if len(sample) < 12:
                sample.append({'material_id': ident, 'formula': formula,
                               'band_gap': r.get('band_gap'), 'density': r.get('density')})
        db.executemany('INSERT INTO materials VALUES ('+','.join('?' for _ in values[0])+')', values)
        db.executemany('INSERT INTO material_elements VALUES (?,?)', elements)
        db.commit(); total += len(values)
        if total % 20480 == 0:
            print('imported', total, flush=True)
    if total != pf.metadata.num_rows:
        raise ValueError('Incomplete import')
    for name, columns in {
        'formula': 'formula_key,deprecated', 'chemsys': 'chemsys,deprecated',
        'elements_count': 'nelements,deprecated', 'crystal': 'crystal_system,deprecated',
        'gap': 'band_gap', 'hull': 'energy_above_hull',
        'formation': 'formation_energy_per_atom', 'density': 'density'
    }.items():
        db.execute(f'CREATE INDEX idx_{name} ON materials ({columns})')
    db.execute('CREATE INDEX idx_element ON material_elements(element,material_id)')
    with source.open('rb') as f:
        digest = hashlib.file_digest(f, 'sha256').hexdigest()
    manifest = dict(database_version=version, source_url=source_url, source_sha256=digest,
                    downloaded_at=datetime.now(timezone.utc).isoformat(),
                    total_records=total, active_records=active, deprecated_records=total-active,
                    licenses=dict(licenses), batch_ids=dict(batches), retained_columns=COLUMNS,
                    source_bytes=source.stat().st_size, sample=sample)
    db.execute('INSERT INTO metadata VALUES (?,?)', ('snapshot', encode(manifest)))
    db.execute('ANALYZE'); db.commit()
    if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise ValueError('SQLite integrity check failed')
    db.close()
    manifest['database_bytes'] = output.stat().st_size
    output.with_suffix('.manifest.json').write_text(encode(manifest))
    print(json.dumps(manifest, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--version', required=True)
    p.add_argument('--source-url', required=True)
    args = p.parse_args()
    build(args.source, args.output, args.version, args.source_url)
