import math
import pandas as pd
import streamlit as st
from pymatgen.core import Element

from frontend.api import request
from frontend.conversations import new_chat
from frontend.periodic_table import render_periodic_table

CRYSTALS = {"Cubic": "立方晶系", "Tetragonal": "四方晶系", "Orthorhombic": "正交晶系", "Hexagonal": "六方晶系", "Trigonal": "三方晶系", "Monoclinic": "单斜晶系", "Triclinic": "三斜晶系"}
FIELDS = {'band_gap': ('带隙', 'eV', 1), 'energy_above_hull': ('Energy above hull', 'meV/atom', 1000),
          'formation_energy_per_atom': ('形成能', 'eV/atom', 1), 'density': ('密度', 'g/cm³', 1)}
COLUMNS = {'formula': '化学式', 'name_zh': '中文名', 'band_gap': '带隙 (eV)',
           'energy_above_hull': 'Ehull (meV/atom)', 'formation_energy_per_atom': '形成能 (eV/atom)',
           'space_group': '空间群', 'crystal_system': '晶系', 'density': '密度 (g/cm³)',
           'volume': '晶胞体积 (Å³)', 'is_metal': '是否金属', 'material_id': 'MP ID'}
OPS = {'≥': 'gte', '>': 'gt', '≤': 'lte', '<': 'lt'}


def number(value, scale=1, digits=2):
    return "暂无数据" if value is None else f"{value * scale:.{digits}f}"


def _defaults():
    st.session_state.setdefault('db_query', '')
    st.session_state.setdefault('db_elements', [])
    st.session_state.setdefault('db_mode', 'exact')
    st.session_state.setdefault('db_crystal', '全部')
    for field in FIELDS:
        for side, op in [('min', '≥'), ('max', '≤')]:
            st.session_state.setdefault(f'{field}_{side}', None)
            st.session_state.setdefault(f'{field}_{side}_op', op)


def _spec():
    filters = {}
    for field, (_, _, scale) in FIELDS.items():
        bounds = {}
        for side in ['min', 'max']:
            value = st.session_state.get(f'{field}_{side}')
            if value is not None:
                bounds[OPS[st.session_state[f'{field}_{side}_op']]] = value / scale
        if bounds:
            filters[field] = bounds
    return {'query': st.session_state.db_query, 'elements': st.session_state.db_elements,
            'element_mode': st.session_state.db_mode, 'filters': filters,
            'crystal_system': None if st.session_state.db_crystal == '全部' else st.session_state.db_crystal}


def _run(spec):
    with st.spinner('正在查询 Materials Project……'):
        result = request('POST', '/materials/explore', json=spec)
    if result:
        st.session_state.search_result = result
        st.session_state.search_page = 1
        return True
    return False


def _load_spec(spec):
    st.session_state.db_query = spec.get('query', '')
    st.session_state.db_elements = spec.get('elements', [])
    st.session_state.db_mode = spec.get('element_mode', 'exact')
    st.session_state.db_crystal = spec.get('crystal_system') or '全部'
    for field, (_, _, scale) in FIELDS.items():
        bounds = spec.get('filters', {}).get(field, {})
        for side, operators, default in [('min', ['gt', 'gte'], '≥'), ('max', ['lt', 'lte'], '≤')]:
            op = next((op for op in operators if op in bounds), None)
            st.session_state[f'{field}_{side}'] = bounds[op] * scale if op else None
            st.session_state[f'{field}_{side}_op'] = next((label for label, value in OPS.items() if value == op), default)


def _apply_draft():
    spec = st.session_state.ai_draft['spec']
    _load_spec(spec)
    if _run(spec):
        st.session_state.pop('ai_draft', None)


def _choose(formula):
    spec = st.session_state.search_result['spec'].copy()
    spec['query'] = formula
    st.session_state.db_query = formula
    _run(spec)


def _clear():
    query = st.session_state.db_query
    _load_spec({'query': query})
    st.session_state.pop('search_result', None)
    st.session_state.pop('ai_draft', None)


def _open(material_id):
    st.session_state.material_id = material_id
    st.session_state.view = 'detail'
    st.session_state.table_nonce = st.session_state.get('table_nonce', 0) + 1


def _analyze():
    new_chat()
    result = st.session_state.search_result
    st.session_state.active_results = {'id': result['result_set_id'], 'count': len(result['candidates']), 'truncated': result['truncated']}
    st.session_state.pop('active_material', None)
    st.session_state.pop('chat_result', None)
    st.session_state.pop('chat_history', None)
    st.session_state.view = 'chat'


def _describe(spec):
    rows = []
    if spec.get('query'):
        rows.append({'条件': '材料 / 化学体系', '识别结果': spec['query']})
    if spec.get('elements'):
        mode = '恰好由这些元素组成' if spec.get('element_mode') == 'exact' else '至少包含这些元素'
        rows.append({'条件': '元素', '识别结果': ', '.join(spec['elements']) + '；' + mode})
    for field, bounds in spec.get('filters', {}).items():
        if field in FIELDS:
            label, unit, scale = FIELDS[field]
            labels = {'gt':'大于（>）','gte':'至少（≥）','lt':'小于（<）','lte':'不超过（≤）'}
            values = '，'.join(f'{labels[op]} {value*scale:g} {unit}' for op, value in bounds.items())
            rows.append({'条件': label, '识别结果': values})
    if spec.get('crystal_system'):
        rows.append({'条件': '晶系', '识别结果': CRYSTALS.get(spec['crystal_system'], spec['crystal_system'])})
    return rows


def _bounds(field):
    label, unit, _ = FIELDS[field]
    st.write(f'{label} ({unit})')
    for side, title, choices in [('min', '最小', ['≥', '>']), ('max', '最大', ['≤', '<'])]:
        op, value = st.columns([1, 2])
        op.selectbox(title, choices, key=f'{field}_{side}_op')
        value.number_input(title + '值', value=None, key=f'{field}_{side}',
                           min_value=None if field == 'formation_energy_per_atom' else 0.0,
                           format='%.4f', step=0.01, placeholder='不限制')


def _results():
    result = st.session_state.get('search_result')
    if not result:
        st.markdown('<div class="results-empty"><h3>先选一种材料，或设定查询条件</h3><p>结果会按晶体结构分别列出，可比较带隙、稳定性与密度。</p><span>例如：磷酸铁锂、TiO2、aluminum nitride</span></div>', unsafe_allow_html=True)
        return
    if result.get('message'):st.caption(result['message'])
    if result['status'] == 'choose_material':
        st.info('你是不是想搜索以下材料？请确认具体组成。')
        for choice in result['choices']:
            st.button(f"{choice.get('alias', choice['formula'])} · {choice['formula']}", key='choice_'+choice['formula'], on_click=_choose, args=(choice['formula'],))
        return
    rows = result.get('candidates', [])
    st.subheader(f'{len(rows)} 个晶体结构')
    if result.get('truncated'):
        st.warning('本次查询达到数量上限，仅展示按 Ehull 排序取得的前 2,000 条范围内结果；请增加条件缩小范围。AI 也仅分析这些结果。')
    if not rows:
        st.info('本次数量限制范围内没有符合条件的材料，请缩小查询范围。' if result.get('truncated') else '没有符合当前条件的材料。可以放宽条件再试。')
        return
    with st.expander('本次查询条件'):
        st.table(_describe(result['spec']))
        st.caption('数据来源：Materials Project；数据库获取时间：'+str(result.get('fetched_at')))
        if result.get('database_version'):
            st.caption('本地快照 · '+str(result['database_version']))
    st.caption('点击表格中的一行进入详情。初始按计算热力学稳定性排序；Ehull=0 不能单独保证实验可合成。')
    st.button('让 AI 分析这些结果', on_click=_analyze)
    columns = st.multiselect('自定义列', list(COLUMNS), default=list(COLUMNS)[:7], format_func=lambda key:COLUMNS[key], key='db_columns')
    if not columns:
        st.warning('请至少选择一列。')
        return
    order = st.selectbox('排序', ['Ehull 升序', '带隙降序', '带隙升序', '形成能升序', '密度升序'])
    field = {'Ehull 升序':'energy_above_hull', '带隙降序':'band_gap', '带隙升序':'band_gap', '形成能升序':'formation_energy_per_atom', '密度升序':'density'}[order]
    descending = order == '带隙降序'
    sorted_rows = sorted(rows, key=lambda r:(r[field] is None, -(r[field] or 0) if descending else r[field] if r[field] is not None else math.inf, r['material_id']))
    pages = math.ceil(len(rows)/50)
    page = st.number_input('结果页', min_value=1, max_value=pages, key='search_page', step=1) if pages > 1 else 1
    current = sorted_rows[(page-1)*50:page*50]
    display = []
    for row in current:
        display.append({**row, 'name_zh':result['names'].get(row['formula'],{}).get('zh') or '—',
                        'energy_above_hull': None if row['energy_above_hull'] is None else row['energy_above_hull']*1000,
                        'crystal_system':CRYSTALS.get(row['crystal_system'],row['crystal_system'])})
    config = {key:st.column_config.Column(label) for key,label in COLUMNS.items()}
    for key in ['band_gap','energy_above_hull','formation_energy_per_atom','density','volume']:
        config[key] = st.column_config.NumberColumn(COLUMNS[key],format='%.4f')
    event = st.dataframe(pd.DataFrame(display),column_order=columns,column_config=config,hide_index=True,
                         on_select='rerun',selection_mode='single-row',width='stretch',
                         key=f"results_{result['result_set_id']}_{page}_{order}_{st.session_state.get('table_nonce',0)}")
    if event.selection.rows:
        _open(current[event.selection.rows[0]]['material_id'])
        st.rerun()


def render_search():
    _defaults()
    st.markdown('<div class="search-heading"><h1>材料探索</h1><p>从名称、组成或性质条件查找材料。</p></div>', unsafe_allow_html=True)
    with st.container(key='search-entry', border=True):
        st.text_input('搜索材料', placeholder='输入中文名、英文名或化学式，例如：磷酸铁锂 / lithium iron phosphate / LiFePO4', key='db_query', max_chars=200)
        with st.expander('AI 生成筛选条件'):
            text = st.text_area('用自然语言找材料', placeholder='找 Li-Fe-O 中 Ehull 小于 25 meV/atom、带隙大于 1 eV 的材料', max_chars=2000)
            if st.button('AI 生成条件'):
                if text.strip():
                    st.session_state.pop('ai_draft', None)
                    with st.spinner('正在解析条件，尚未查询材料数据库……'):
                        draft = request('POST','/materials/parse-filters',json={'text':text})
                    if draft:
                        st.session_state.ai_draft = draft
                else:
                    st.warning('请输入筛选需求。')
            draft = st.session_state.get('ai_draft')
            if draft:
                st.table(_describe(draft['spec']))
                if draft['clarification']:
                    st.warning(draft['clarification'])
                else:
                    st.caption('以上为待确认草稿，尚未查询 MP；应用后会同步左侧条件。')
                    st.button('应用 AI 筛选',on_click=_apply_draft)

    with st.container(key='periodic-choice'):
        render_periodic_table()
    left, right = st.columns([1.15,3], gap="large")
    with left:
        with st.container(key='search-filters', border=True):
            st.subheader('按性质筛选')
            st.multiselect('元素', [Element.from_Z(z).symbol for z in range(1,119)], key='db_elements')
            st.radio('元素模式', ['exact','contains'], format_func=lambda v:'恰好由这些元素组成' if v=='exact' else '至少包含这些元素',key='db_mode')
            _bounds('band_gap')
            _bounds('energy_above_hull')
            st.selectbox('晶系', ['全部']+list(CRYSTALS),format_func=lambda v:CRYSTALS.get(v,v),key='db_crystal')
            with st.expander('更多属性'):
                _bounds('formation_energy_per_atom')
                _bounds('density')
            st.button('查询材料',type='primary',on_click=lambda:_run(_spec()), use_container_width=True)
            st.button('重置条件',on_click=_clear, use_container_width=True)
    with right:
        with st.container(key='search-results', border=True):
            _results()
