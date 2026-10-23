"""Read-only MP snapshot adapter. Records stay on disk, not in a Python cache."""
import json
import os
from pathlib import Path
import sqlite3
from contextlib import contextmanager
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
NUMERIC = {'band_gap', 'formation_energy_per_atom', 'energy_above_hull', 'density'}


def id_formats(material_id):
    """MP AlphaID is base 26 (a=0); accept both official representations."""
    suffix = material_id.removeprefix('mp-')
    if suffix.isdigit():
        number = int(suffix)
    elif suffix and suffix.isalpha() and suffix.isascii() and suffix.islower():
        number = 0
        for char in suffix:
            number = number * 26 + ord(char) - ord('a')
    else:
        raise ValueError('无效材料 ID')
    value = number
    chars = ''
    while value:
        value, remainder = divmod(value, 26)
        chars = chr(97 + remainder) + chars
    alpha = 'mp-' + (chars or 'a').rjust(8, 'a')
    return f'mp-{number}', alpha


def enabled():
    return os.getenv('MP_DATA_BACKEND', 'api') == 'local'


@contextmanager
def connect():
    path = Path(os.getenv('MP_SNAPSHOT_PATH', str(ROOT / 'data/mp_snapshot/materials.sqlite')))
    db = sqlite3.connect('file:' + quote(str(path.resolve())) + '?mode=ro', uri=True, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA cache_size=-2048')
    try:
        yield db
    finally:
        db.close()


def metadata(db):
    return json.loads(db.execute("SELECT value FROM metadata WHERE key='snapshot'").fetchone()[0])


def _row(record, meta, detail=False):
    out = {key: record[key] for key in (
        'material_id', 'formula', 'band_gap', 'formation_energy_per_atom',
        'energy_above_hull', 'density', 'volume', 'space_group',
        'space_group_number', 'crystal_system')}
    legacy, alpha = id_formats(record['material_id'])
    # Match MP's backwards-compatible display for previously published IDs.
    out['material_id'] = legacy if int(legacy[3:]) <= 3347529 else alpha
    out['source_material_id'] = record['material_id']
    out['is_metal'] = None if record['is_metal'] is None else bool(record['is_metal'])
    out.update(source='Materials Project', data_access='local_snapshot',
               database_version=meta['database_version'], source_url=meta['source_url'])
    builder = json.loads(record['builder_meta']) if record['builder_meta'] else {}
    out.update(source_license=builder.get('license'), source_batch=builder.get('batch_id'))
    if detail:
        out.update(number_of_sites=record['number_of_sites'],
                   is_stable=None if record['is_stable'] is None else bool(record['is_stable']),
                   lattice_parameters=json.loads(record['lattice_parameters']) if record['lattice_parameters'] else None)
    return out


def search(*, material_id=None, formula=None, chemsys=None, elements=None,
           exact_elements=False, crystal_system=None, filters=None, limit=None,
           return_meta=False):
    from pymatgen.core import Composition
    where = ['m.deprecated=0']
    args = []
    if material_id:
        where.append('m.material_id IN (?,?)'); args.extend(id_formats(material_id.strip()))
    if formula:
        where.append('m.formula_key=?'); args.append(Composition(formula).reduced_formula)
    if chemsys:
        where.append('m.chemsys=?'); args.append('-'.join(sorted(chemsys.split('-'))))
    if elements:
        for symbol in sorted(set(elements)):
            where.append('EXISTS (SELECT 1 FROM material_elements e WHERE e.material_id=m.material_id AND e.element=?)')
            args.append(symbol)
        if exact_elements:
            where.append('m.nelements=?'); args.append(len(set(elements)))
    if crystal_system:
        where.append('m.crystal_system=? COLLATE NOCASE'); args.append(str(getattr(crystal_system, 'value', crystal_system)))
    operators = {'lt': '<', 'lte': '<=', 'gt': '>', 'gte': '>='}
    for field, bounds in (filters or {}).items():
        if field not in NUMERIC:
            raise ValueError('不支持的材料属性')
        for op, value in bounds.items():
            if op not in operators:
                raise ValueError('不支持的筛选运算符')
            where.append('m.' + field + operators[op] + '?'); args.append(value)
    sql = 'SELECT m.* FROM materials m WHERE ' + ' AND '.join(where)
    sql += ' ORDER BY m.energy_above_hull IS NULL,m.energy_above_hull,m.material_id'
    if limit is not None:
        sql += ' LIMIT ?'; args.append(limit)
    with connect() as db:
        meta = metadata(db)
        rows = [_row(r, meta) for r in db.execute(sql, args)]
    if return_meta:
        return dict(candidates=rows, limit_reached=bool(limit and len(rows) >= limit),
                    fetched_at=meta['downloaded_at'], cached=False, data_access='local_snapshot',
                    database_version=meta['database_version'])
    return rows


def detail(material_id):
    with connect() as db:
        meta = metadata(db)
        record = db.execute('SELECT * FROM materials WHERE material_id IN (?,?) AND deprecated=0', id_formats(material_id)).fetchone()
        return dict(data=_row(record, meta, detail=True) if record else None,
                    fetched_at=meta['downloaded_at'], cached=False,
                    data_access='local_snapshot', database_version=meta['database_version'])


def snapshot_info():
    with connect() as db:
        return metadata(db)
