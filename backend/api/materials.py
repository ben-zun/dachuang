import re

from fastapi import APIRouter, HTTPException
from openai import OpenAIError
from pydantic import BaseModel, Field
from backend.materials.explorer import ExploreSpec, explore, parse_filters
from mp_api.client.core.client import MPRestError
from requests.exceptions import RequestException

from backend.materials import aliases, cache
from backend.materials.names import pubchem, wikidata
from backend.materials.resolver import resolve_material
from backend.tools.materials_project import search_materials_project, get_material_detail

router = APIRouter(prefix="/materials")


def _summary(resolution):
    kind, value = resolution["kind"], resolution["value"]
    lookup = search_materials_project(**{kind: value}, return_meta=True)
    rows = lookup['candidates']
    rows.sort(key=lambda row: (row["energy_above_hull"] is None,
                              row["energy_above_hull"] if row["energy_above_hull"] is not None else float("inf"), row["material_id"]))
    return {"status": "found" if rows else "not_found", "lookup_status": "ok" if rows else "no_database_record", "message": resolution.get("repair_notice") if rows else "Materials Project 当前查询没有匹配记录，不代表材料不存在。", "resolution": resolution,
            "names": aliases.names(rows[0]["formula"]) if rows else {},
            "candidates": rows, "fetched_at": lookup.get('fetched_at'),
            "cached": lookup.get('cached', False),
            "data_access": lookup.get('data_access', 'api'),
            "database_version": lookup.get('database_version')}


def _choices(query, resolution):
    rows = resolution.get("suggestions") or [{"formula": value, "alias": value} for value in resolution.get("candidates", [])]
    if resolution["source"] == "ambiguous_alias":
        rows = [{"formula": row["formula"], "alias": aliases.names(row["formula"]).get("zh") or row["formula"]} for row in rows]
    return {"status": "choose_material", "lookup_status": "ambiguous_match", "query": query, "source": resolution["source"], "choices": rows}


def search(query, client):
    if not isinstance(query, str) or not query.strip() or len(query) > 200:
        raise ValueError("请输入不超过 200 字符的材料名称或化学式")
    resolution = resolve_material(query)
    if resolution["kind"] != "unresolved":
        return {"query": query, **_summary(resolution)}
    if resolution.get("candidates"):
        return _choices(query, resolution)

    key = aliases.normalize(query)
    hit = cache.get("external_name", key)
    entries = hit["data"] if hit else []
    if not hit:
        for provider in (pubchem, wikidata):
            try:
                proposed = provider(query)
            except (RequestException, ValueError, KeyError):
                proposed = []
            for entry in proposed:
                verified = _summary({"kind": "formula", "value": entry["formula"], "source": entry["source"]})
                if not verified["candidates"]:
                    continue
                # Wikidata 搜索的近似命中也只作为候选，不自动把相关实体当成所搜材料。
                if provider == wikidata and key not in {aliases.normalize(name) for name, _ in entry["aliases"]}:
                    entry["needs_confirmation"] = True
                entries.append(entry)
            if entries:
                break
        cache.put("external_name", key, entries, ttl=cache.TTL if entries else 1800)
    if entries:
        formulas = {entry["formula"] for entry in entries}
        if len(formulas) == 1 and not any(e.get("needs_confirmation") for e in entries):
            for entry in entries:
                for name, lang in entry["aliases"]:
                    aliases.add(name, entry["formula"], lang, entry["source"], entry["source_url"])
                aliases.add(query, entry["formula"], "zh" if re.search(r"[\u4e00-\u9fff]", query) else "en", entry["source"], entry["source_url"])
            return {"query": query, **_summary({"kind": "formula", "value": next(iter(formulas)), "source": entries[0]["source"]})}
        return _choices(query, {"source": "external_candidates", "candidates": sorted(formulas)})

    # 模型仅产生候选；每项先验证 MP，再让用户选择。
    hit = cache.get("llm_name", key)
    if hit:
        return hit["data"]
    resolution = resolve_material(query, client=client)
    confirmed = []
    for formula in resolution.get("candidates", []):
        candidate = _summary({"kind": "formula", "value": formula, "source": "llm_candidate"})
        if candidate["candidates"]:
            confirmed.append(formula)
    result = _choices(query, {"source": "llm_candidate", "candidates": confirmed}) if confirmed else {"status": "not_found", "lookup_status": "no_exact_match", "query": query, "candidates": [], "message": "没有匹配到这个名称，可以尝试化学式；这不表示材料不存在。"}
    cache.put("llm_name", key, result, ttl=1800)
    return result


def detail(material_id):
    if not re.fullmatch(r"mp-(?:\d+|[a-z]{6,})", material_id):
        raise ValueError("无效材料 ID")
    entry = get_material_detail(material_id)
    if entry["data"] is None:
        raise HTTPException(status_code=404, detail="未找到该材料结构")
    return {**entry, "names": aliases.names(entry["data"]["formula"])}


@router.get("/search")
def search_route(query: str):
    from backend.api.main import client
    try:
        return search(query, client)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (MPRestError, RequestException, OpenAIError) as exc:
        raise HTTPException(status_code=502, detail="材料数据服务暂时不可用，请稍后重试。") from exc


class ParseFilterRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@router.post("/explore")
def explore_route(spec: ExploreSpec):
    from backend.api.main import client
    try:
        return explore(spec, client)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (MPRestError, RequestException, OpenAIError) as exc:
        raise HTTPException(status_code=502, detail="材料数据服务暂时不可用，请稍后重试。") from exc


@router.post("/parse-filters")
def parse_filters_route(request: ParseFilterRequest):
    from backend.api.main import client
    try:
        return parse_filters(request.text, client)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OpenAIError as exc:
        raise HTTPException(status_code=502, detail="AI 条件解析失败，请稍后重试。") from exc


@router.get("/{material_id}")
def detail_route(material_id: str):
    try:
        return detail(material_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (MPRestError, RequestException, OpenAIError) as exc:
        raise HTTPException(status_code=502, detail="材料数据服务暂时不可用，请稍后重试。") from exc
