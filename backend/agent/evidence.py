def summarize_database(result):
    # 网页保留完整表；给模型提供真实范围和代表结构，避免长列表导致误读。
    rows = result.get("candidates", [])
    if not rows:
        return result
    representatives = {rows[0]["material_id"]: rows[0]}
    ranges = {}
    for field in ("band_gap", "energy_above_hull", "formation_energy_per_atom", "density"):
        available = [row for row in rows if row[field] is not None]
        if available:
            low, high = min(available, key=lambda r: r[field]), max(available, key=lambda r: r[field])
            ranges[field] = {"min": low[field], "max": high[field]}
            for row in (low, high):
                representatives[row["material_id"]] = row
    groups = {rows[0]["space_group"]}
    for row in rows:
        if row["space_group"] not in groups and len(groups) < 3:
            representatives[row["material_id"]] = row
            groups.add(row["space_group"])
    summary = {key: value for key, value in result.items() if key != "candidates"}
    return {**summary, "candidate_count": len(rows), "ranges": ranges,
            "representative_candidates": list(representatives.values()),
            "all_candidates_in_table": True,
            "zero_ehull_count": sum(row.get("energy_above_hull") == 0 for row in rows)}







def database_source(result, number):
    rows=result.get('candidates',[])
    return {'id':f'DB{number}','evidence_id':f'DB-{number}','citation':f'[DB{number}]','title':'Materials Project',
            'material_query':result.get('material_query'),'status':result.get('status'),
            'entries':[{'material_id':r['material_id'],'formula':r['formula'],
                        'url':f"https://materialsproject.org/materials/{r['material_id']}"} for r in rows],
            'provenance':result.get('resolution',{})}




def database_answer(materials, database_sources):
    """纯数据库结果直接展示查询值，防止模型改写数字、单位或添加理论。"""
    labels={'band_gap':'带隙','formation_energy_per_atom':'形成能','energy_above_hull':'Ehull',
            'space_group':'空间群','space_group_number':'空间群编号','density':'密度','crystal_system':'晶系'}
    units={'band_gap':'eV','formation_energy_per_atom':'eV/atom','energy_above_hull':'eV/atom','density':'g/cm³'}
    systems={'Triclinic':'三斜','Monoclinic':'单斜','Orthorhombic':'正交','Tetragonal':'四方','Trigonal':'三方','Hexagonal':'六方','Cubic':'立方'}
    def display(row,keys):
        values=[]
        for key in keys:
            value=row.get(key)
            if value is None:value='暂无数据'
            elif isinstance(value,(int,float)):value=f'{value:.6g}'
            else:value=systems.get(value,str(value))
            values.append(f"{labels[key]}：{value} {units.get(key,'')}".strip())
        return '；'.join(values)
    parts=[]
    for result,source in zip(materials,database_sources):
        if result.get('status')!='ok' or not result.get('primary'):continue
        rows=result['candidates'];row=result['primary'];keys=result.get('properties') or ['band_gap','energy_above_hull']
        scope='当前选中结构' if result.get('selected_structure_only') else f"查询到 {len(rows)} 个结构"
        if result.get('resolution',{}).get('repair_notice'):parts.append(result['resolution']['repair_notice'])
        parts.append(f"**{result['material_query']}**：{scope}。主结果（最低 Ehull）：{row['formula']} · {row['material_id']} · 空间群 {row.get('space_group') or '暂无数据'}。\n\n{display(row,keys)} [{source['id']}]。")
        if len(rows)>1:
            parts.append('该组成/查询有多个结构，不能用主结果代表全部结构；完整结果见下方数据表。')
            if result.get('filters') or len(rows)<=20:
                parts.append('\n'.join(f"- {r['material_id']} · {r['formula']} · {display(r,keys)} [{source['id']}]" for r in rows[:20]))
                if len(rows)>20:parts.append(f'以上展示前 20 项，完整 {len(rows)} 项见下方数据表。')
        source['cited']=True
    return '\n\n'.join(parts)
