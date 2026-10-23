from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st

from frontend.api import request
from frontend.conversations import new_chat
from frontend.material_search import number, CRYSTALS


def _back():
    st.session_state.view = "search"


def _ask(entry):
    new_chat()
    st.session_state.active_material = {**entry["data"], "names": entry["names"]}
    st.session_state.pop("active_results", None)
    st.session_state.pop("chat_history", None)
    st.session_state.view = "chat"
    st.session_state.pop("chat_result", None)


def render_detail():
    st.button("← 返回搜索结果", on_click=_back)
    entry = request("GET", "/materials/" + st.session_state.material_id)
    if not entry:
        return
    row, names = entry["data"], entry["names"]
    st.header(row["formula"])
    for name in (names.get("zh"), names.get("en")):
        if name:
            st.write(name)
    st.write(f"{CRYSTALS.get(row['crystal_system'], row['crystal_system'])} · {row['space_group']}")
    st.button("向 AI 询问此材料", on_click=_ask, args=(entry,))
    st.caption(f"Materials Project ID：{row['material_id']}")
    columns = st.columns(4)
    columns[0].metric("带隙 (eV)", number(row["band_gap"]), help="MP 计算的电子带隙，不等同于实验测量值。")
    columns[1].metric("Ehull (meV/atom)", number(row["energy_above_hull"], 1000), help="相对于竞争相凸包的能量；数值较低表示该计算参考体系下较稳定，不能单独保证实验可合成。")
    columns[2].metric("形成能 (eV/atom)", number(row["formation_energy_per_atom"]))
    columns[3].metric("密度 (g/cm³)", number(row["density"]))
    st.caption("MP 计算数据；Ehull 描述计算参考体系中的稳定性，不能单独保证实验可合成。")
    st.subheader("晶体结构")
    st.table({"属性": ["晶系", "空间群", "空间群编号", "晶胞体积 (Å³)", "原子位点数"],
              "数值": [CRYSTALS.get(row["crystal_system"], row["crystal_system"]) or "暂无数据",
                       row["space_group"] or "暂无数据", str(row["space_group_number"]),
                       number(row["volume"]), str(row["number_of_sites"])]})
    lattice = row.get("lattice_parameters")
    if lattice:
        st.table({"参数": list(lattice), "数值": [round(v, 4) for v in lattice.values()],
                  "单位": ["Å"]*3 + ["°"]*3})
    st.subheader("稳定性与电子性质")
    stability = "位于计算凸包上" if row["is_stable"] is True else "高于计算凸包" if row["is_stable"] is False else "暂无数据"
    metal = "是" if row["is_metal"] is True else "否" if row["is_metal"] is False else "暂无数据"
    st.write(f"Formation energy：{number(row['formation_energy_per_atom'], digits=4)} eV/atom")
    st.write(f"Energy above hull：{number(row['energy_above_hull'], 1000, 4)} meV/atom")
    st.write(f"Band gap：{number(row['band_gap'])} eV")
    st.write(f"MP is_stable：{stability}；Is metal：{metal}")
    st.subheader("数据来源")
    fetched = datetime.fromisoformat(entry["fetched_at"]).astimezone(ZoneInfo("Asia/Shanghai"))
    st.write("Materials Project")
    if entry.get('data_access') == 'local_snapshot':
        st.caption('本地数据快照 · '+str(entry.get('database_version', '')))
    if row.get('source_license') == 'BY-NC':
        st.caption('此条目来自 GNoME（BY-NC），仅用于非商业教学科研展示。')
    st.write(f"获取时间：{fetched.strftime('%Y-%m-%d %H:%M:%S')}（北京时间）")
    st.write(f"Material ID：{row['material_id']}")
    st.link_button("查看 Materials Project 原始条目", f"https://next-gen.materialsproject.org/materials/{row['material_id']}")
