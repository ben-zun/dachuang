import re
import unicodedata

from pymatgen.core import Composition, Element

from backend.materials.aliases import lookup, fuzzy, connect


def _formula(value):
    composition = Composition(unicodedata.normalize("NFKC", value), strict=True)
    if not composition.valid or not composition.num_atoms or any(n <= 0 for n in composition.values()):
        raise ValueError("无效化学式")
    return composition.reduced_formula


def resolve_material(material_query, client=None, _phase=True):
    """确定性解析优先；模型补充的候选需用户确认，不当成确定名称。"""
    if not isinstance(material_query, str) or not material_query.strip():
        raise ValueError("材料表达不能为空")
    query = unicodedata.normalize("NFKC", material_query).strip()
    from backend.materials.phases import resolve_phase
    qualified=resolve_phase(query,lambda base:resolve_material(base,_phase=False),modifiers=_phase)
    if qualified:return qualified
    if re.fullmatch(r"mp-(?:\d+|[a-z]{6,})", query):
        return {"kind": "material_id", "value": query, "source": "mp_id"}
    with connect() as db:
        formulas={r[0] for r in db.execute("SELECT DISTINCT formula FROM aliases")}
    exact=[f for f in formulas if f.casefold()==query.casefold()]
    repaired=[f for f in formulas if f.casefold()==query.casefold().replace('0','o')]
    if len(exact)==1 and query!=exact[0]:
        return {"kind":"formula","value":exact[0],"source":"formula_case_repair","repair_notice":"按化学式 "+exact[0]+" 理解你的输入。","requires_database_verification":True}
    if not exact and len(repaired)==1:
        return {"kind":"formula","value":repaired[0],"source":"formula_typo_repair","repair_notice":"我猜你指的是 "+repaired[0]+"，先按这个查询。","requires_database_verification":True}
    try:
        if re.search(r'[A-Za-z]0\d',query):raise ValueError('疑似 O/0 输入误差')
        return {"kind": "formula", "value": _formula(query), "source": "formula"}
    except ValueError:
        pass
    for element in Element:
        if query.casefold() == element.long_name.casefold():
            return {'kind': 'formula', 'value': element.symbol, 'source': 'element_name'}
    matches = lookup(query)
    if len(matches) == 1:
        return {"kind": "formula", "value": matches[0]["formula"], "source": "alias"}
    if matches:
        return {"kind": "unresolved", "value": None, "source": "ambiguous_alias", "candidates": [r["formula"] for r in matches], "suggestions": matches}
    elements = query.split("-")
    if len(elements) > 1 and len(set(elements)) == len(elements):
        try:
            for symbol in elements:
                Element(symbol)
            return {"kind": "chemsys", "value": "-".join(sorted(elements)), "source": "chemsys"}
        except ValueError:
            pass

    from backend.materials.chemical_names import binary_name
    named_formula=binary_name(query)
    if named_formula:
        return {'kind':'formula','value':named_formula,'source':'compositional_binary_name','name_rule':'explicit_or_fixed_valence'}
    suggestions = fuzzy(query)
    if suggestions:
        best=suggestions[0]; runner=next((s['score'] for s in suggestions[1:] if s['formula']!=best['formula']),0)
        from rapidfuzz.distance import Levenshtein
        close=Levenshtein.distance(query.casefold(),best['alias'].casefold())==1 and len(query)>=4
        # A chemically meaningful changed element is ambiguity, not a spelling fix.
        changed_elements=set(query)-set(best['alias'])
        if changed_elements & set('氢氦锂铍硼碳氮氧氟氖钠镁铝硅磷硫氯氩钾钙钛钒铬锰铁钴镍铜锌银锡碘钡钨铂金汞铅'):close=False
        chemical_change=bool(changed_elements & set('锂钠钙镁铝铁钴镍铜锌钛钡铅锡硫磷氮氧氟氯'))
        if not chemical_change and (best['score']>=90 or close) and best['score']-runner>=12:
            return {"kind":"formula","value":best['formula'],"source":"alias_typo_repair",
                    "repair_notice":"我猜你指的是 "+best['alias']+"（"+best['formula']+"），先按这个查询。",
                    "requires_database_verification":True,"suggestions":suggestions}
        return {"kind": "unresolved", "value": None, "source": "fuzzy_alias",
                "candidates": [row["formula"] for row in suggestions], "suggestions": suggestions}

    candidates = []
    if client is not None:
        result = client.chat_json([
            {"role": "system", "content": '把材料名称识别为化学式候选。仅返回 JSON：{"candidates":["化学式"]}。最多3项，未知、泛称、合金或无法唯一对应组成时可以返回空列表。不要编造。'},
            {"role": "user", "content": query},
        ])
        suggested = result.get("candidates", []) if isinstance(result, dict) else []
        if not isinstance(suggested, list):
            suggested = []
        for candidate in suggested[:3]:
            try:
                value = _formula(candidate)
            except (ValueError, TypeError):
                continue
            if value not in candidates:
                candidates.append(value)
    return {"kind": "unresolved", "value": None, "source": "llm_candidate" if candidates else "unknown", "candidates": candidates}
