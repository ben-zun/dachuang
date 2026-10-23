import os
import math
from pathlib import Path

from dotenv import load_dotenv
from mp_api.client import MPRester
from backend.materials import cache

ROOT = Path(__file__).resolve().parents[2]
FIELDS = [
    "material_id", "formula_pretty", "band_gap",
    "formation_energy_per_atom", "energy_above_hull", "density", "symmetry", "volume", "is_metal",
]


def search_materials_project(*, material_id=None, formula=None, chemsys=None, elements=None,
                             exact_elements=False, crystal_system=None, filters=None,
                             use_cache=True, limit=None, return_meta=False):
    """按 MP ID、化学式或化学体系查询，返回普通字典列表；无匹配时返回空列表。

    带隙单位为 eV，形成能和 hull 能量为 eV/atom，密度为 g/cm³。
    """
    supplied = {"material_ids": material_id, "formula": formula, "chemsys": chemsys}
    supplied = {key: value for key, value in supplied.items() if value is not None}
    if len(supplied) > 1 or not (supplied or elements or filters or crystal_system):
        raise ValueError("请提供 material_id、formula 或 chemsys 中的一项")
    query = {}
    if supplied:
        field, value = next(iter(supplied.items()))
        if not isinstance(value, str) or not value.strip():
            raise ValueError("查询内容必须是非空字符串")
        query[field] = [value.strip()] if field == "material_ids" else value.strip()
    if elements:
        from pymatgen.core import Element
        elements = sorted(set(elements))
        for symbol in elements:
            Element(symbol)
        query["elements"] = elements
        if exact_elements:
            query["num_elements"] = len(elements)
    if crystal_system:
        from emmet.core.symmetry import CrystalSystem
        query["crystal_system"] = CrystalSystem(crystal_system)
    if limit is not None and (not isinstance(limit, int) or not 1 <= limit <= 2001):
        raise ValueError("查询数量限制无效")
    filters = {} if filters is None else filters
    if not isinstance(filters, dict):
        raise ValueError("filters 必须是对象")
    filter_query = {}
    api_names = {"band_gap": "band_gap", "formation_energy_per_atom": "formation_energy", "energy_above_hull": "energy_above_hull", "density": "density"}
    for name, bounds in filters.items():
        if name not in api_names or not isinstance(bounds, dict) or not bounds:
            raise ValueError("不支持的筛选条件")
        for op, number in bounds.items():
            if op not in ("lt", "lte", "gt", "gte") or isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number):
                raise ValueError("筛选条件必须使用 lt/lte/gt/gte 和有限数值")
        lower = max((v for k, v in bounds.items() if k in ("gt", "gte")), default=None)
        upper = min((v for k, v in bounds.items() if k in ("lt", "lte")), default=None)
        if lower is not None and upper is not None and lower > upper:
            raise ValueError("筛选下限不能大于上限")
        # MP 的范围包含端点；以相邻浮点数排除严格边界，避免数量上限被端点条目占满。
        if lower is not None and bounds.get("gt") == lower:
            lower = math.nextafter(float(lower), math.inf)
        if upper is not None and bounds.get("lt") == upper:
            upper = math.nextafter(float(upper), -math.inf)
        filter_query[api_names[name]] = (lower, upper)

    load_dotenv(ROOT / ".env")
    from backend.materials import local_store
    if local_store.enabled():
        return local_store.search(material_id=material_id, formula=formula, chemsys=chemsys,
                                  elements=elements, exact_elements=exact_elements,
                                  crystal_system=crystal_system, filters=filters,
                                  limit=limit, return_meta=return_meta)
    api_key = os.getenv("MP_API_KEY")
    if not api_key:
        raise RuntimeError("没有找到 MP_API_KEY，请填写项目 .env")

    cache_key = cache.query_key(query={key: getattr(value, "value", value) for key, value in query.items()}, filters=filter_query, limit=limit)
    hit = cache.get("mp_search_v2", cache_key) if use_cache else None
    cached = bool(hit)
    if hit:
        results = hit["data"]
    else:
        with MPRester(api_key=api_key, mute_progress_bars=True) as mpr:
            docs = mpr.materials.summary.search(
                **query, **filter_query, fields=FIELDS, all_fields=False, deprecated=False,
                **({"num_chunks": math.ceil(limit / 1000), "chunk_size": min(limit, 1000), "_sort_fields": "energy_above_hull"} if limit else {}),
            )

        results = []
        for doc in docs:
            symmetry = doc.symmetry
            results.append({
                "material_id": str(doc.material_id),
                "formula": doc.formula_pretty,
                "band_gap": doc.band_gap,
                "formation_energy_per_atom": doc.formation_energy_per_atom,
                "energy_above_hull": doc.energy_above_hull,
                "space_group": symmetry.symbol if symmetry else None,
                "space_group_number": symmetry.number if symmetry else None,
                "density": doc.density,
                "volume": doc.volume, "is_metal": doc.is_metal,
                "crystal_system": symmetry.crystal_system.value if symmetry and symmetry.crystal_system else None,
                "source": "Materials Project",
            })
        if limit:
            results.sort(key=lambda r: (r["energy_above_hull"] is None, r["energy_above_hull"] if r["energy_above_hull"] is not None else math.inf, r["material_id"]))
            results = results[:limit]
        if use_cache:
            hit = cache.put("mp_search_v2", cache_key, results, ttl=cache.TTL if results else 1800)
    # 最后再检查原始边界，避免数据库与浮点序列化差异。
    comparisons = {"lt": lambda x, y: x < y, "lte": lambda x, y: x <= y,
                   "gt": lambda x, y: x > y, "gte": lambda x, y: x >= y}
    filtered = [row for row in results if all(
        row[name] is not None and all(comparisons[op](row[name], value) for op, value in bounds.items())
        for name, bounds in filters.items()
    )]
    if return_meta:
        return {"candidates": filtered, "limit_reached": bool(limit and len(results) >= limit),
                "fetched_at": hit["fetched_at"] if hit else None, "cached": cached}
    return filtered



PROPERTIES = ["band_gap", "formation_energy_per_atom", "energy_above_hull", "space_group", "space_group_number", "density", "crystal_system"]
SEARCH_MATERIALS_TOOL = {
    "type": "function",
    "function": {
        "name": "search_materials",
        "description": "查询具体材料、化学式、中文材料名或化学体系的数据库信息。查询具体材料的带隙、形成能、稳定性、晶体结构，或按条件筛选时使用。纯概念解释不调用。material_query 必须原样摘取用户的材料表达，不要把中文名称翻译成化学式。",
        "parameters": {
            "type": "object",
            "properties": {
                "material_query": {"type": "string", "description": "原始材料表达，例如磷酸铁锂、LiFePO4、二氧化钛、Li-Fe-O、mp-149。"},
                "properties": {"type": "array", "items": {"type": "string", "enum": PROPERTIES}, "description": "稳定性查询使用 energy_above_hull；晶体结构使用 space_group 和 crystal_system。"},
                "filters": {
                    "type": "object",
                    "properties": {
                        name: {"type": "object", "properties": {op: {"type": "number"} for op in ["lt", "lte", "gt", "gte"]}, "additionalProperties": False}
                        for name in ["band_gap", "formation_energy_per_atom", "energy_above_hull", "density"]
                    },
                    "additionalProperties": False,
                    "description": "没有筛选时为 {}。带隙用 eV，能量用 eV/atom，密度用 g/cm³。25 meV/atom 转为 0.025 eV/atom；小于使用 lt，例如 {energy_above_hull: {lt: 0.025}}。",
                },
            },
            "required": ["material_query", "properties", "filters"],
            "additionalProperties": False,
        },
    },
}


def search_materials(material_query, properties=None, filters=None, client=None):
    from backend.materials.resolver import resolve_material

    properties = [] if properties is None else properties
    if not isinstance(properties, list) or any(name not in PROPERTIES for name in properties):
        raise ValueError("不支持的材料属性")
    resolution = resolve_material(material_query, client=client)
    result = {"material_query": material_query, "resolution": resolution,
              "properties": properties, "filters": filters or {}, "candidates": [],
              "primary": None, "multiple_structures": False}
    if resolution["kind"] == "unresolved":
        options=resolution.get('candidates',[])
        if resolution.get('source')=='llm_candidate':
            verified=[f for f in options if search_materials_project(formula=f,limit=1)]
            resolution['candidates']=options=verified
            resolution['candidate_verification']='MP composition exists; name correspondence still unconfirmed'
        status='ambiguous_match' if options else 'no_exact_match'
        message=("这个名称可能对应 "+"、".join(options)+"；可以先说明它们的区别，具体数值需要选定组成。" if options else "暂时没有匹配到这个名称；可以先解释相关原理，提供化学式后能继续查具体数据。")
        return {**result,'status':status,'message':message}
    records = search_materials_project(**{resolution["kind"]: resolution["value"]}, filters=filters)
    if resolution.get('phase'):
        from backend.materials.phases import filter_candidates
        result['phase_constraint']=resolution['phase']
        result['unfiltered_candidate_count']=len(records)
        records=filter_candidates(records,resolution['phase'])
        result['phase_match_method']='composition + sourced name constraint + MP space_group_number; candidate screen, not full prototype verification'
    records.sort(key=lambda row: (row["energy_above_hull"] is None,
                                 row["energy_above_hull"] if row["energy_above_hull"] is not None else float("inf"),
                                 row["material_id"]))
    return {**result, "status": "ok" if records else "no_database_record", "message": resolution.get("repair_notice") if records else "Materials Project 当前查询没有匹配记录，不代表材料不存在。", "candidates": records,
            "primary": records[0] if records else None,
            "multiple_structures": resolution["kind"] == "formula" and len(records) > 1}


def get_material_detail(material_id):
    load_dotenv(ROOT / ".env")
    from backend.materials import local_store
    if local_store.enabled():
        return local_store.detail(str(material_id))
    key = str(material_id)
    hit = cache.get("mp_detail", key)
    if hit:
        return hit
    load_dotenv(ROOT / ".env")
    api_key = os.getenv("MP_API_KEY")
    if not api_key:
        raise RuntimeError("没有找到 MP_API_KEY，请填写项目 .env")
    with MPRester(api_key=api_key, mute_progress_bars=True) as mpr:
        docs = mpr.materials.summary.search(
            material_ids=[key], all_fields=False, deprecated=False,
            fields=FIELDS + ["structure", "nsites", "is_stable"],
        )
    if not docs:
        return cache.put("mp_detail", key, None, ttl=1800)
    doc = docs[0]
    symmetry = doc.symmetry
    lattice = doc.structure.lattice if doc.structure else None
    result = {
        "material_id": str(doc.material_id), "formula": doc.formula_pretty,
        "band_gap": doc.band_gap, "formation_energy_per_atom": doc.formation_energy_per_atom,
        "energy_above_hull": doc.energy_above_hull, "density": doc.density,
        "space_group": symmetry.symbol if symmetry else None,
        "space_group_number": symmetry.number if symmetry else None,
        "crystal_system": symmetry.crystal_system.value if symmetry and symmetry.crystal_system else None,
        "lattice_parameters": {key: getattr(lattice, key) for key in ["a", "b", "c", "alpha", "beta", "gamma"]} if lattice else None,
        "volume": doc.volume, "number_of_sites": doc.nsites,
        "is_stable": doc.is_stable, "is_metal": doc.is_metal, "source": "Materials Project",
    }
    return cache.put("mp_detail", key, result)
