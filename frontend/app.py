import re
import streamlit as st

from frontend.api import request, stream_chat
from frontend.references import link_answer, cited_ids, display_markdown
import requests
from frontend.material_search import render_search, COLUMNS
from frontend.material_detail import render_detail

from pathlib import Path
from html import escape
from frontend.conversations import initialize, save_current, new_chat, open_chat, delete_chat, compact_result

st.set_page_config(page_title="材料物理智能助手", layout="wide", initial_sidebar_state="expanded")
st.markdown("<style>" + Path(__file__).with_name("porcelain.css").read_text() + "</style>", unsafe_allow_html=True)

def _render_reference(source, key):
    panel = st.expander(f"[{source.get('evidence_id') or source['number']}] {source['title']}", key=key, on_change='rerun')
    if not panel.open:
        return
    with panel:
        st.caption(" / ".join(source["section_path"]))
        if source.get("pages"): st.caption("原文页码：" + "、".join(map(str,source["pages"])))
        st.caption("用途：" + ("正文文字可参考；复杂公式和图表数值尚未核验" if source.get("permission")=="background" else "检索匹配的参考片段；仍需核对原文与适用条件"))
        st.markdown(f"[打开原始文档]({source['source_url']})")
        if source.get("cited"):
            st.caption("已选为可核对的资料线索")
        if source.get("support_quote"):
            st.caption("原文匹配的摘句（不等于独立核实全部结论）")
            st.write(source["support_quote"])
        st.markdown(source["text"])
        st.caption("片段：" + ", ".join(source["chunk_ids"]))



def _render_result(result, key):
    st.caption("材料物理助手")
    if result.get("error"):
        st.warning("本次回答可能未完整生成，请结合工具状态核对，必要时重新提问。")
    sources = result.get("sources", [])
    links = {str(source["number"]): source["source_url"] for source in sources}
    links.update({source['evidence_id']: source['source_url'] for source in sources if source.get('evidence_id')})
    for source in result.get("database_sources", []):
        entries = source["entries"]
        if len(entries) == 1:
            links[source["id"]] = entries[0]["url"]
    cited = cited_ids(result["answer"])
    sources = [{**source, "cited": str(source.get("evidence_id") or source["number"]) in cited} for source in sources]
    core_calls = [tool for tool in result.get("tools", []) if tool.get("name") == "search_core_knowledge"]
    if core_calls:
        used = sum(bool(source.get("cited")) for source in sources)
        if sources:
            st.caption(f"知识库：已检索到 {len(sources)} 条片段 · 正文引用 {used} 条")
        else:
            statuses = "、".join(dict.fromkeys(str(tool.get("result", {}).get("status", "unknown")) for tool in core_calls))
            st.caption(f"知识库：本轮已尝试检索，未取得参考片段（{statuses}）")
    else:
        st.caption("知识库：本轮未调用")
    answer = link_answer(result["answer"], links)
    st.markdown(display_markdown(answer))
    current_id = result.get("task_status", {}).get("active_material_id")
    if current_id: st.caption("本轮对话已确认结构：" + current_id)
    tools = result.get("task_status", {}).get("tools", [])
    if any(tool["status"] not in ("ok", "success") for tool in tools):
        st.warning("部分查询需要确认或未完成，请查看候选条目和工具状态；已有结果仍可参考。")
    if tools:
        with st.expander("本次工具与任务状态"):
            for tool in tools:
                st.caption(tool["name"] + " · " + str(tool["status"]))
    if result.get('streamed') and result.get('raw_answer') != result.get('answer'):
        st.caption('上方为完成格式与工具核对后的最终回答，可能与生成中的预览略有不同。')
    for tool in result.get('tools', []):
        receipt = tool.get('result', {})
        options = receipt.get('resolution', {}).get('candidates', [])
        if tool.get('name') == 'search_materials' and options and not receipt.get('candidates'):
            st.subheader('待确认的材料名称候选')
            st.dataframe([{'候选组成': value, '状态': '名称对应关系待确认；未取得该结构性质'} for value in options], hide_index=True)
    if result.get("calculations"):
        panel = st.expander("计算输入、结果与适用条件", key=key+'_calc', on_change='rerun')
        if panel.open:
            with panel:
                for calculation in result["calculations"]:
                    st.json(calculation)
    database_sources = result.get("database_sources", [])
    if database_sources:
        st.subheader("数据来源")
        for source in database_sources:
            panel = st.expander(f"[{source['id']}] Materials Project · {source.get('material_query') or '查询'}", key=key+'_db_'+source['id'], on_change='rerun')
            if panel.open:
                with panel:
                    st.caption("状态：" + str(source["status"]))
                    st.markdown('\n\n'.join(f"{entry['formula']} · [{entry['material_id']}]({entry['url']})" for entry in source['entries']))
    if sources:
        st.subheader("参考资料")
        if not any(source.get('cited') for source in sources):
            st.info('本轮检索到了资料，但回答没有标注来源；这些片段不能视为对整段回答的核实。')
        else:
            st.caption('点击回答中的编号可打开原文；请核对引用对应的具体陈述。')
        for source in sources:
            if source.get("cited"): _render_reference(source, key+'_ref_'+str(source['number']))
        unused = [source for source in sources if not source.get("cited")]
        if unused:
            panel = st.expander(f"其他检索片段（{len(unused)} 条，未被回答引用）", key=key+'_unused', on_change='rerun')
            if panel.open:
                with panel:
                    for source in unused: _render_reference(source, key+'_ref_'+str(source['number']))
    # Only this response's receipts belong in the current result table.
    material_rows = {row['material_id']: row for material in result.get('materials', [])
                     for row in material.get('candidates', [])}
    if material_rows:
        rows = sorted(material_rows.values(), key=lambda row: (row.get('energy_above_hull') is None, row.get('energy_above_hull') or 0, row['material_id']))
        st.subheader('本轮材料候选与性质')
        st.caption(f"已取得的 Materials Project 数据 · {len(rows)} 个条目。带隙 eV；能量 eV/atom；密度 g/cm³。")
        versions = sorted({row.get('database_version') for row in rows if row.get('data_access') == 'local_snapshot' and row.get('database_version')})
        if versions:
            st.caption('Materials Project 本地快照 · '+', '.join(versions))
        if any(row.get('source_license') == 'BY-NC' for row in rows):
            st.caption('部分条目来自 GNoME（BY-NC），仅用于非商业教学科研展示。')
        labels = {**COLUMNS, "energy_above_hull": "Ehull (eV/atom)", "space_group_number": "空间群编号", "source": "来源", "structure_label": "晶型/结构"}
        st.caption('候选按 Ehull 从低到高排列；多个条目不等于查询失败，排序也不代表已选定唯一晶型。')
        fields = ('material_id','formula','band_gap','formation_energy_per_atom','energy_above_hull','space_group','crystal_system','density','structure_label')
        panel = st.expander(f'完整候选表 · {len(rows)} 个结构', key=key+'_candidates', on_change='rerun')
        if panel.open:
            with panel:
                st.dataframe([{labels.get(field, field): row.get(field) for field in fields} for row in rows], hide_index=True, height=330, column_config={labels[field]: st.column_config.NumberColumn(format="%.4f") for field in ("band_gap", "formation_energy_per_atom", "energy_above_hull", "density")})

    if result.get("literature"):
        st.subheader("论文检索结果")
        st.caption("以下是检索到的真实文献线索；摘要或元数据不等于全文核验。")
        for paper in result["literature"]:
            with st.expander(f"{paper.get('year') or '年份未知'} · {paper['title']}"):
                st.write("作者：" + "; ".join(paper["authors"]))
                st.markdown(f"[原始记录]({paper['url']})")
                if paper.get("doi"): st.write("DOI：" + paper["doi"])
                st.caption(f"被引次数：{paper['citation_count']}（OpenAlex 检索时快照）")
                st.write("开放获取：" + ("是" if paper["open_access"] else "否 / 未确认"))
                st.caption("发表类型：" + str(paper.get("source_type") or "未确认") + "；发表元数据尚未独立审阅")
                if paper.get("open_access_url"): st.markdown(f"[开放版本]({paper['open_access_url']})")
                st.write(paper.get("abstract") or "该记录没有摘要，可打开原始记录核对。")


def _go(view):
    st.session_state.view = view


def _clear_chat():
    new_chat()


def _inspector(result):
    st.markdown('<div class="inspector-label">当前探索</div>', unsafe_allow_html=True)
    rows = {r['material_id']: r for m in (result or {}).get('materials', []) for r in m.get('candidates', [])}
    active_id = (result or {}).get('task_status', {}).get('active_material_id')
    selected = rows.get(active_id)
    if not selected and len(rows) == 1:
        selected = next(iter(rows.values()))
    if rows:
        formulas = list(dict.fromkeys(r.get('formula', '') for r in rows.values()))
        st.markdown('<div class="material-heading">' + escape(' / '.join(formulas)) + '</div>', unsafe_allow_html=True)
        st.caption(f"Materials Project · {len(rows)} 个真实条目")
        if selected:
            st.caption("当前确认条目")
            st.markdown('**' + selected['material_id'] + '**')
            for label, key in (("空间群", "space_group"), ("晶系", "crystal_system"), ("结构标签", "structure_label")):
                if selected.get(key): st.write(f"{label}：{selected[key]}")
        else:
            st.caption("多个结构候选，尚未选定唯一条目。")
    else:
        st.markdown('<div class="quiet-panel">材料与资料</div>', unsafe_allow_html=True)
        st.caption("查询后，可在这里查看材料条目和来源。")
    st.divider()
    sources = (result or {}).get('sources', [])
    st.caption("本轮资料")
    st.write(f"参考片段 {len(sources)} 条")
    st.write(f"计算记录 {len((result or {}).get('calculations', []))} 条")
    st.divider()
    st.caption("可以问什么")
    st.write("理解概念与方法")
    st.write("查询材料结构与性质")
    st.write("核对数值、单位与条件")


initialize()
save_current()
with st.sidebar:
    st.markdown('<div class="brand-space" aria-hidden="true"></div>', unsafe_allow_html=True)
    with st.container(key="sidebar-nav"):
        st.button("新建对话", on_click=_clear_chat, use_container_width=True, type="primary")
        st.button("智能问答", on_click=_go, args=("chat",), use_container_width=True)
        st.button("材料探索", on_click=_go, args=("search",), use_container_width=True)
    st.markdown('<div class="sidebar-label">最近对话</div>', unsafe_allow_html=True)
    with st.container(key="chat-history"):
        saved = st.session_state.saved_chats
        if not saved:
            st.caption("还没有对话记录")
        for ident, chat in reversed(list(saved.items())):
            selected = ident == st.session_state.current_chat
            title = chat['title']
            title_col, delete_col = st.columns([5, 1], gap='small')
            with title_col:
                st.button(title[:22] + ('…' if len(title) > 22 else ''),
                         key='history_' + ident, help=f"{title} · {chat['turn_count']} 轮问答",
                         type='primary' if selected else 'secondary', use_container_width=True,
                         on_click=open_chat, args=(ident,))
            with delete_col:
                st.button('×', key='delete_'+ident, help='删除这段对话', on_click=delete_chat, args=(ident,), use_container_width=True)
    with st.container(key="sidebar-note"):
        st.caption("材料物理智能助手")
        st.markdown("查材料 · 问原理 · 核对计算")
        st.caption("历史已保存 · 此浏览器可恢复")

view = st.session_state.get('view', 'chat')
st.markdown('<div class="workspace-bar"><span>材料助手 / <b>' + ('智能问答' if view == 'chat' else '材料探索') + '</b></span><span>Qwen3.7 Flash</span></div>', unsafe_allow_html=True)
if view == 'search':
    render_search()
elif view == 'detail':
    render_detail()
else:
    center, inspector = st.columns([3.7, 1.25], gap="large")
    turns = st.session_state.setdefault('chat_turns', [])
    # Keep an already generated response when upgrading an open browser session.
    if not turns and st.session_state.get('chat_result'):
        history = st.session_state.get('chat_history', [])
        question = next((m['content'] for m in reversed(history) if m['role'] == 'user'), '上一轮问题')
        turns.append({'question': question, 'result': st.session_state.chat_result})
    with inspector:
        _inspector(st.session_state.get('chat_result'))
    with center:
        active = st.session_state.get('active_material')
        results_context = st.session_state.get('active_results')
        if active:
            st.info(f"已选材料：{active['formula']} · {active['material_id']}")
        if results_context:
            st.caption(f"当前筛选结果：{results_context['count']} 个结构")
        if not turns:
            st.markdown('<div class="welcome"><h1>今天想了解什么材料？</h1><p>查材料、问原理，也可以核对计算。</p></div>', unsafe_allow_html=True)
            for left, right in [(('理解一个概念', '形成能可以判断材料稳定性吗？'), ('探索一种材料', '帮我查一下磷酸铁锂的带隙和稳定性。')), (('做一次可靠计算', '将 -0.7 eV/atom 换算为 MgCl2 的 eV/化学式单位。'), ('从资料里找答案', '介绍几种常见超导材料及其应用条件。'))]:
                cols = st.columns(2)
                for col, (title, question) in zip(cols, (left, right)):
                    with col:
                        if st.button(question, use_container_width=True):
                            st.session_state.pending_question = question
                            st.rerun()
        for i, turn in enumerate(turns):
            with st.chat_message('user'): st.write(turn['question'])
            with st.chat_message('assistant'):
                with st.container(key=f'answer_{i}'):
                    _render_result(turn['result'], key=f'turn_{st.session_state.current_chat}_{i}')
        input_col, mode_col = st.columns([4, 1], gap='small', vertical_alignment='center')
        with input_col:
            typed = st.chat_input('输入问题，或继续追问…', max_chars=10000)
        with mode_col:
            enhanced = st.toggle('知识库增强', key='knowledge_enhanced', help='开启后，材料概念、机制和方法问题优先查阅知识库；关闭时由常规流程按需检索。闲聊和纯计算不额外检索。')
        question = typed or st.session_state.pop('pending_question', None)
        if question:
            with st.chat_message('user'): st.write(question)
            with st.chat_message('assistant'):
                status = st.empty()
                output = st.empty()
                completed = {}
                draft = ''
                try:
                    for event, data in stream_chat({'session_id': st.session_state.get('chat_session_id'), 'message': question, 'knowledge_mode': 'enhanced' if enhanced else 'auto', 'history': st.session_state.get('chat_history', [])[-6:], 'active_material_id': active['material_id'] if active else None, 'active_results_id': results_context['id'] if results_context else None}):
                        if event == 'status': status.caption(data['message'])
                        elif event == 'reset':
                            draft = ''; output.empty(); status.caption(data['message'])
                        elif event == 'delta':
                            draft += data['text']; output.markdown(display_markdown(draft) + ' ▌')
                        elif event == 'done': completed.update(data)
                        elif event == 'error': raise RuntimeError(data['message'])
                    if not completed: raise RuntimeError('连接中断，回答尚未完成，请重试。')
                    completed = compact_result(completed)
                    st.session_state.chat_result = completed
                    st.session_state.chat_session_id = completed.get('session_id')
                    turns.append({'question': question, 'result': completed})
                    history = st.session_state.get('chat_history', [])
                    st.session_state.chat_history = (history + [{'role': 'user', 'content': question}, {'role': 'assistant', 'content': completed['answer'][:10000]}])[-6:]
                except (requests.RequestException, RuntimeError, ValueError) as exc:
                    status.empty(); st.error(str(exc))
                    st.caption('本次回答未加入历史，请重试。')
                else:
                    save_current()
                    st.rerun()
        st.caption("Enter 发送 · Shift + Enter 换行")
