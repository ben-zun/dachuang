"""Check every imported record against the retained official summary fields."""
import json
import argparse
import sqlite3
import pyarrow.parquet as pq
from pathlib import Path

root = Path('/opt/materials-assistant/data/mp_snapshot')
parser = argparse.ArgumentParser()
parser.add_argument('--database', type=Path, default=root / 'materials.sqlite')
parser.add_argument('--source', type=Path, default=root / 'build/summary.parquet')
args = parser.parse_args()
db = sqlite3.connect(args.database)
db.row_factory = sqlite3.Row
fields = ['band_gap', 'formation_energy_per_atom', 'energy_above_hull', 'density',
          'volume', 'is_stable', 'is_metal', 'energy_per_atom',
          'uncorrected_energy_per_atom', 'is_magnetic', 'total_magnetization', 'is_gap_direct']
columns = ['material_id', 'formula_pretty', 'deprecated', 'symmetry', 'structure.lattice', 'nsites', 'elements'] + fields
checked = 0
for batch in pq.ParquetFile(args.source).iter_batches(batch_size=1024, columns=columns, use_threads=False):
    for original in batch.to_pylist():
        row = db.execute('SELECT * FROM materials WHERE material_id=?', (original['material_id'],)).fetchone()
        assert row is not None
        for name in fields:
            assert row[name] == original[name], (original['material_id'], name)
        assert row['formula'] == original['formula_pretty']
        assert row['deprecated'] == bool(original['deprecated'])
        assert row['number_of_sites'] == original['nsites']
        for dest, src in [('space_group','symbol'), ('space_group_number','number'), ('crystal_system','crystal_system')]:
            assert row[dest] == (original['symmetry'] or {}).get(src)
        lattice = (original.get('structure') or {}).get('lattice') or {}
        params = {k: lattice[k] for k in ['a','b','c','alpha','beta','gamma'] if lattice.get(k) is not None}
        assert (json.loads(row['lattice_parameters']) if row['lattice_parameters'] else {}) == params
        elements = {r[0] for r in db.execute('SELECT element FROM material_elements WHERE material_id=?', (original['material_id'],))}
        assert elements == set(original['elements'])
        checked += 1
assert checked == db.execute('SELECT count(*) FROM materials').fetchone()[0]
result = {'records_compared': checked, 'field_mismatches': 0, 'integrity_check': db.execute('PRAGMA integrity_check').fetchone()[0]}
(root / 'parity_audit.json').write_text(json.dumps(result, indent=2))
print(json.dumps(result))
