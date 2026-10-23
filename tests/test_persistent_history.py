import json
import os
from pathlib import Path
import sqlite3
import tempfile
import types
import unittest
from contextlib import nullcontext
from unittest.mock import patch
from frontend import history_store, conversations

class State(dict):
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__

class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'CHAT_HISTORY_DB':str(Path(self.tmp.name)/'history.sqlite')})
        self.env.start()
    def tearDown(self):
        self.env.stop(); self.tmp.cleanup()
    def test_storage_isolation_and_reopen(self):
        history_store.save('a'*64,'1'*32,{'title':'甲','state':{'chat_turns':[]},'turn_count':1})
        self.assertEqual(history_store.load('b'*64),{})
        self.assertEqual(history_store.load('a'*64)['1'*32]['title'],'甲')
        with sqlite3.connect(history_store.database_path()) as db:
            self.assertNotEqual(db.execute('select owner from chats').fetchone()[0],'a'*64)
    def test_reject_bad_identity(self):
        with self.assertRaises(ValueError):history_store.load("' OR 1=1")
    def test_refresh_restores_and_switches(self):
        state=State();fake=types.SimpleNamespace(session_state=state, container=lambda **kwargs:nullcontext())
        with patch.object(conversations,'st',fake),patch.object(conversations,'_identity',return_value='a'*64):
            conversations.initialize()
            first=state.current_chat
            state.chat_turns=[{'question':'第一题','result':{'answer':'第一答'}}]
            state.chat_session_id='c'*32
            conversations.save_current();conversations.new_chat()
            state.chat_turns=[{'question':'第二题','result':{'answer':'第二答'}}]
            conversations.save_current()
            state.clear() # refresh / new frontend process
            conversations.initialize()
            self.assertEqual(state.chat_turns[0]['question'],'第二题')
            conversations.open_chat(first)
            self.assertEqual(state.chat_turns[0]['result']['answer'],'第一答')
            self.assertEqual(state.chat_session_id,'c'*32)
        state.clear()
        with patch.object(conversations,'st',fake),patch.object(conversations,'_identity',return_value='b'*64):
            conversations.initialize();self.assertEqual(state.saved_chats,{})

    def test_delete_is_scoped_and_survives_refresh(self):
        state=State();fake=types.SimpleNamespace(session_state=state, container=lambda **kwargs:nullcontext())
        with patch.object(conversations,'st',fake),patch.object(conversations,'_identity',return_value='a'*64):
            conversations.initialize()
            first=state.current_chat
            state.chat_turns=[{'question':'保留','result':{'answer':'回答'}}]
            conversations.save_current();conversations.new_chat()
            second=state.current_chat
            state.chat_turns=[{'question':'删除','result':{'answer':'回答'}}]
            conversations.save_current()
            history_store.save('b'*64,second,{'title':'另一个浏览器','state':{},'turn_count':1})
            conversations.delete_chat(second)
            self.assertEqual(state.current_chat,first)
            conversations.save_current()
            self.assertNotIn(second,history_store.load('a'*64))
            self.assertIn(second,history_store.load('b'*64))
            state.clear();conversations.initialize()
            self.assertEqual(state.chat_turns[0]['question'],'保留')

    def test_unchanged_history_is_not_written_twice(self):
        state=State();fake=types.SimpleNamespace(session_state=state, container=lambda **kwargs:nullcontext())
        with patch.object(conversations,'st',fake),patch.object(conversations,'_identity',return_value='a'*64):
            conversations.initialize();state.chat_turns=[{'question':'题','result':{'answer':'答'}}]
            with patch.object(history_store,'save') as save:
                conversations.save_current();conversations.save_current()
                self.assertEqual(save.call_count,1)

if __name__=='__main__':unittest.main()
