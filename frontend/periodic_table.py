"""点击周期表与元素多选框共享同一状态，只改条件，不执行查询。"""
import streamlit as st
from pymatgen.core import Element


def _toggle(symbol):
    selected = list(st.session_state.get('db_elements', []))
    if symbol in selected:
        selected.remove(symbol)
    else:
        selected.append(symbol)
    st.session_state.db_elements = selected


def render_periodic_table():
    panel = st.expander('周期表选元素', key='periodic_open', on_change='rerun')
    if not panel.open:
        return
    with panel:
        selected = st.session_state.get('db_elements', [])
        for period in range(1, 8):
            columns = st.columns(18, gap='small')
            for z in range(1, 119):
                element = Element.from_Z(z)
                if element.row != period or 57 < z <= 71 or 89 < z <= 103:
                    continue
                with columns[element.group-1]:
                    st.button(element.symbol, key=f'element_{z}', help=element.long_name,
                              use_container_width=True,
                              type='primary' if element.symbol in selected else 'secondary',
                              on_click=_toggle, args=(element.symbol,))
        for title, start, stop in [('镧系', 58, 72), ('锕系', 90, 104)]:
            st.caption(title)
            columns = st.columns(14, gap='small')
            for index, z in enumerate(range(start, stop)):
                element = Element.from_Z(z)
                with columns[index]:
                    st.button(element.symbol, key=f'element_{z}', help=element.long_name,
                              use_container_width=True,
                              type='primary' if element.symbol in selected else 'secondary',
                              on_click=_toggle, args=(element.symbol,))
