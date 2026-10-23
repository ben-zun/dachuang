"""Exercise the real Streamlit app using synthetic UI fixtures and no paid calls."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
from frontend import conversations, api, history_store

ROOT = Path(__file__).resolve().parents[1]


class ChatUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'CHAT_HISTORY_DB': self.temp.name+'/history.sqlite'})
        self.env.start()
        self.identity = patch.object(conversations, '_identity', return_value='f'*64)
        self.identity.start()

    def tearDown(self):
        self.identity.stop(); self.env.stop(); self.temp.cleanup()

    def app(self):
        return AppTest.from_file(str(ROOT/'frontend/app.py'), default_timeout=30).run()

    def submit(self, at, result):
        payloads = []
        def stream(payload):
            payloads.append(payload)
            yield 'delta', {'text': '生成中的预览'}
            yield 'done', result
        with patch.object(api, 'stream_chat', side_effect=stream):
            at.chat_input[0].set_value('测试问题').run()
        self.assertEqual(len(at.exception), 0)
        return payloads[0]

    def receipt(self, index):
        # Deliberately synthetic display fixtures, not material reference data.
        if index == 0:
            source = {'number': 1, 'evidence_id': 'E1', 'title': 'UI fixture',
                      'source_url': 'https://example.com/ui-fixture',
                      'section_path': ['Fixture section'], 'pages': [],
                      'permission': 'background', 'chunk_ids': ['fixture-1'],
                      'text': 'Synthetic source text used only to test lazy rendering.'}
            return {'answer': 'UI fixture answer. [E1]', 'sources': [source],
                    'tools': [{'name': 'search_core_knowledge', 'result': {
                        'status': 'success', 'retrieval_audit': {'fixture': True}}}]}
        rows = [{'material_id': f'fixture-{i}', 'formula': 'LiFePO4',
                 'band_gap': None, 'energy_above_hull': None} for i in range(70)]
        return {'answer': 'Synthetic candidate display.', 'sources': [],
                'materials': [{'candidates': rows}], 'tools': []}

    def test_mode_reaches_request_and_references_are_lazy(self):
        at = self.app()
        self.assertFalse(at.toggle(key='knowledge_enhanced').value)
        result = self.receipt(0)
        payload = self.submit(at, result)
        self.assertEqual(payload['knowledge_mode'], 'auto')
        quote = result['sources'][0]['text']
        self.assertFalse(any(quote in m.value for m in at.markdown))
        self.assertNotIn('retrieval_audit', at.session_state['chat_result']['tools'][0]['result'])
        ref = next(e for e in at.expander if e.key and '_ref_' in e.key)
        at.session_state[ref.key] = True
        at.run()
        self.assertTrue(any(quote in m.value for m in at.markdown))
        at.toggle(key='knowledge_enhanced').set_value(True).run()
        self.assertEqual(self.submit(at, result)['knowledge_mode'], 'enhanced')

    def test_full_candidates_are_lazy_and_delete_persists(self):
        at = self.app(); result = self.receipt(7)
        self.submit(at, result)
        self.assertEqual(len(at.dataframe), 0)
        panel = next(e for e in at.expander if e.key and e.key.endswith('_candidates'))
        at.session_state[panel.key] = True
        at.run()
        self.assertEqual(len(at.exception), 0)
        self.assertEqual(len(at.dataframe[0].value), 70)
        ident = at.session_state['current_chat']
        at.button(key='delete_'+ident).click().run()
        self.assertEqual(len(at.exception), 0)
        self.assertNotIn(ident, history_store.load('f'*64))
        self.assertEqual(len(self.app().session_state['chat_turns']), 0)

    def test_periodic_table_is_lazy_and_selection_works(self):
        at = self.app()
        next(b for b in at.button if b.label == '材料探索').click().run()
        self.assertEqual(len(at.exception), 0)
        self.assertFalse(any(b.key == 'element_26' for b in at.button))
        at.session_state['periodic_open'] = True
        at.run()
        self.assertEqual(len([b for b in at.button if b.key and b.key.startswith('element_')]), 118)
        at.button(key='element_26').click().run()
        self.assertIn('Fe', at.session_state['db_elements'])
        # AppTest does not send expander widget state on a button interaction.
        at.session_state['periodic_open'] = True
        at.run()
        at.button(key='element_26').click().run()
        self.assertNotIn('Fe', at.session_state['db_elements'])
