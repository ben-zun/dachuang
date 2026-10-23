"""Browser-scoped persistent history without accounts."""
from copy import deepcopy
from uuid import uuid4
import streamlit as st
import streamlit.components.v1 as components
from pathlib import Path
import re
from frontend import history_store

_identity = components.declare_component('materials_browser_identity', path=str(Path(__file__).with_name('browser_identity')))

KEYS = ('chat_result', 'chat_history', 'chat_session_id', 'chat_turns', 'active_material', 'active_results')


def compact_result(result):
    """Keep display data; full retrieval/debug receipts remain in backend logs."""
    out = dict(result)
    out['tools'] = [{**{k: v for k, v in tool.items() if k != 'result'},
        'result': {k: v for k, v in tool.get('result', {}).items()
                   if k in ('status', 'resolution', 'candidates')}} for tool in result.get('tools', [])]
    for tool in out['tools']:
        if 'candidates' in tool['result']:
            tool['result']['candidates'] = [{k: row.get(k) for k in ('material_id', 'formula')}
                                           for row in tool['result']['candidates']]
    if 'task_status' in out:
        out['task_status'] = {k: v for k, v in out['task_status'].items() if k != 'delivery_contract'}
    return out


def initialize():
    with st.container(key='browser-identity'):
        token = _identity(key='browser_identity', default=None)
    if token is None:
        st.stop()
    if not isinstance(token, str) or not re.fullmatch('[a-f0-9]{64}', token):
        st.error('请允许本站保存浏览器数据后刷新，以便隔离并恢复你的对话记录。')
        st.stop()
    if st.session_state.get('history_owner') != token:
        st.session_state.history_owner = token
        st.session_state.saved_chats = history_store.load(token)
        st.session_state.current_chat = uuid4().hex
        # Restore the most recently saved conversation after refresh/restart.
        if st.session_state.saved_chats:
            ident = next(reversed(st.session_state.saved_chats))
            st.session_state.update(deepcopy(st.session_state.saved_chats[ident]['state']))
            st.session_state.current_chat = ident
            # Search result handles can expire independently of chat history.
            st.session_state.pop('active_results', None)
    st.session_state.setdefault('saved_chats', {})
    st.session_state.setdefault('current_chat', uuid4().hex)


def save_current():
    turns = st.session_state.get('chat_turns', [])
    if not turns:
        return
    signature = (st.session_state.current_chat, len(turns),
                 turns[-1]['result'].get('request_id'), turns[-1]['result'].get('answer'))
    if st.session_state.get('_history_saved_signature') == signature:
        return
    st.session_state.saved_chats[st.session_state.current_chat] = {
        'title': turns[0]['question'],
        'turn_count': len(turns),
        'state': {key: deepcopy(st.session_state[key]) for key in KEYS if key in st.session_state},
    }
    history_store.save(st.session_state.history_owner, st.session_state.current_chat, st.session_state.saved_chats[st.session_state.current_chat])
    st.session_state['_history_saved_signature'] = signature


def new_chat():
    save_current()
    for key in (*KEYS, 'pending_question'):
        st.session_state.pop(key, None)
    st.session_state.current_chat = uuid4().hex
    st.session_state.view = 'chat'


def open_chat(ident):
    save_current()
    saved = st.session_state.saved_chats.get(ident)
    if not saved:
        return
    for key in (*KEYS, 'pending_question'):
        st.session_state.pop(key, None)
    st.session_state.update(deepcopy(saved['state']))
    st.session_state.current_chat = ident
    st.session_state.pop('active_results', None)
    st.session_state.view = 'chat'


def delete_chat(ident):
    history_store.delete(st.session_state.history_owner, ident)
    st.session_state.saved_chats.pop(ident, None)
    if st.session_state.current_chat == ident:
        for key in (*KEYS, 'pending_question', '_history_saved_signature'):
            st.session_state.pop(key, None)
        st.session_state.current_chat = uuid4().hex
        if st.session_state.saved_chats:
            last = next(reversed(st.session_state.saved_chats))
            st.session_state.update(deepcopy(st.session_state.saved_chats[last]['state']))
            st.session_state.current_chat = last
            st.session_state.pop('active_results', None)
        st.session_state.view = 'chat'
