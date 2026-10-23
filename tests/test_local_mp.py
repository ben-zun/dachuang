import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from backend.materials import local_store
from backend.tools.materials_project import search_materials_project, get_material_detail


class LocalMPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'materials.sqlite'
        spec = importlib.util.spec_from_file_location('builder', Path(__file__).resolve().parents[1] / 'deploy/build_mp_snapshot.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with sqlite3.connect(self.path) as db:
            db.executescript(module.SCHEMA)
            meta = {'database_version': 'test-version', 'downloaded_at': '2026-10-23T00:00:00+00:00', 'source_url': 'official-test-fixture'}
            db.execute('INSERT INTO metadata VALUES (?,?)', ('snapshot', json.dumps(meta)))
            for ident, formula, chemsys, gap, hull, crystal, deprecated in [
                ('mp-1', 'TiO2', 'O-Ti', 2.0, 0, 'Tetragonal', 0),
                ('mp-2', 'TiO2', 'O-Ti', 2.5, 0.025, 'Orthorhombic', 0),
                ('mp-3', 'TiO2', 'O-Ti', None, None, 'Tetragonal', 0),
                ('mp-4', 'TiO2', 'O-Ti', 9, 0, 'Tetragonal', 1),
                ('mp-5', 'LiFePO4', 'Fe-Li-O-P', 3.9, 0, 'Orthorhombic', 0),
            ]:
                fields = {'material_id': ident, 'formula': formula, 'formula_key': formula,
                          'chemsys': chemsys, 'nelements': len(chemsys.split('-')),
                          'band_gap': gap, 'energy_above_hull': hull, 'crystal_system': crystal,
                          'deprecated': deprecated, 'is_metal': False, 'is_stable': hull == 0,
                          'lattice_parameters': json.dumps({'a': 4.2}), 'number_of_sites': 6}
                db.execute('INSERT INTO materials ('+','.join(fields)+') VALUES ('+','.join('?' for _ in fields)+')', list(fields.values()))
                db.executemany('INSERT INTO material_elements VALUES (?,?)', [(ident, e) for e in chemsys.split('-')])
        self.env = patch.dict(os.environ, {'MP_DATA_BACKEND': 'local', 'MP_SNAPSHOT_PATH': str(self.path)})
        self.env.start()

    def tearDown(self):
        self.env.stop(); self.temp.cleanup()

    def test_no_network_and_polymorphs(self):
        with patch('backend.tools.materials_project.MPRester', side_effect=AssertionError('network forbidden')):
            rows = search_materials_project(formula='O2Ti', use_cache=False)
        self.assertEqual([r['material_id'] for r in rows], ['mp-1', 'mp-2', 'mp-3'])
        self.assertEqual(rows[0]['database_version'], 'test-version')

    def test_strict_boundaries_and_missing_values(self):
        for op, ids in [('lt', ['mp-1']), ('lte', ['mp-1', 'mp-2']), ('gt', []), ('gte', ['mp-2'])]:
            with self.subTest(op=op):
                rows = search_materials_project(formula='TiO2', filters={'energy_above_hull': {op: 0.025}})
                self.assertEqual([r['material_id'] for r in rows], ids)

    def test_elements_chemsys_crystal_and_limit(self):
        self.assertEqual(len(search_materials_project(elements=['O', 'Li'], exact_elements=False)), 1)
        self.assertEqual(search_materials_project(elements=['O', 'Li'], exact_elements=True), [])
        rows = search_materials_project(chemsys='Ti-O', crystal_system='Tetragonal', limit=1, return_meta=True)
        self.assertEqual(rows['candidates'][0]['material_id'], 'mp-1')
        self.assertTrue(rows['limit_reached'])

    def test_detail_and_unknown(self):
        row = get_material_detail('mp-1')['data']
        self.assertEqual(row['lattice_parameters']['a'], 4.2)
        self.assertTrue(row['is_stable'])
        self.assertIsNone(get_material_detail('mp-4')['data'])
        self.assertIsNone(get_material_detail('mp-99999')['data'])

    def test_bad_filters_are_still_rejected(self):
        with self.assertRaises(ValueError):
            search_materials_project(formula='TiO2', filters={'band_gap': {'lt': float('inf')}})
        with self.assertRaises(ValueError):
            search_materials_project(formula='TiO2', filters={'fake_field': {'lt': 3}})

    def test_official_alpha_ids_and_legacy_ids_are_equivalent(self):
        self.assertEqual(local_store.id_formats('mp-149'), ('mp-149', 'mp-aaaaaaft'))
        self.assertEqual(local_store.id_formats('mp-ft'), ('mp-149', 'mp-aaaaaaft'))
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE materials SET material_id='mp-aaaaaaab' WHERE material_id='mp-1'")
        self.assertEqual(get_material_detail('mp-1')['data']['material_id'], 'mp-1')
        self.assertEqual(get_material_detail('mp-aaaaaaab')['data']['material_id'], 'mp-1')
        self.assertEqual(search_materials_project(material_id='mp-1')[0]['source_material_id'], 'mp-aaaaaaab')


if __name__ == '__main__':
    unittest.main()
