"""外部名称数据只提出组成，必须由调用方验证 MP 后才能入库。"""
import re
import time
from threading import Lock
from urllib.parse import quote

import requests

from backend.materials.resolver import _formula

HEADERS = {"User-Agent": "MaterialsAgent/0.1 (personal scientific materials project; operator yxzhang)"}


_REQUEST_LOCK = Lock()
_LAST_REQUEST = 0.0


def _pubchem_get(url, timeout):
    global _LAST_REQUEST
    for attempt in range(3):
        with _REQUEST_LOCK:
            time.sleep(max(0, 0.5 - (time.monotonic() - _LAST_REQUEST)))
            _LAST_REQUEST = time.monotonic()
        response = requests.get(url, headers=HEADERS, timeout=timeout)
        if response.status_code not in (429, 503) or attempt == 2:
            return response
        time.sleep(2 * (attempt + 1))


def pubchem(query, timeout=6, by_formula=False):
    base = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound"
    path = f"name/{quote(query, safe='')}"
    if by_formula:
        response = _pubchem_get(f"{base}/fastformula/{quote(query, safe='')}/cids/JSON", timeout)
        if response.status_code == 404:
            return []
        response.raise_for_status()
        cids = response.json().get("IdentifierList", {}).get("CID", [])[:3]
        if not cids:
            return []
        path = "cid/" + ",".join(map(str, cids))
    response = _pubchem_get(f"{base}/{path}/property/MolecularFormula/JSON", timeout)
    if response.status_code == 404:
        return []
    response.raise_for_status()
    results = []
    for row in response.json().get("PropertyTable", {}).get("Properties", [])[:3]:
        try:
            formula = _formula(row["MolecularFormula"])
        except ValueError:
            continue
        if by_formula and formula != _formula(query):
            continue
        cid = row["CID"]
        response = _pubchem_get(f"{base}/cid/{cid}/synonyms/JSON", timeout)
        response.raise_for_status()
        synonyms = response.json().get("InformationList", {}).get("Information", [{}])[0].get("Synonym", [])
        aliases = [name for name in synonyms if 2 < len(name) <= 100 and re.search(r"[A-Za-z]{3}", name)
                   and not re.match(r"^(CID|CHEBI|UNII|MFCD|DSSTox|NSC|EINECS|EC )", name, re.I)][:30]
        results.append({"formula": formula, "aliases": [(name, "en") for name in aliases],
                        "source": "pubchem", "source_url": f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}"})
    return results


def wikidata_entity(entity_id, timeout=6):
    url = f"https://www.wikidata.org/wiki/Special:EntityData/{entity_id}.json"
    response = requests.get(url, headers=HEADERS, timeout=timeout)
    response.raise_for_status()
    entity = response.json()["entities"][entity_id]
    formulas = []
    for claim in entity.get("claims", {}).get("P274", []):
        if "datavalue" in claim["mainsnak"]:
            try:
                formulas.append(_formula(claim["mainsnak"]["datavalue"]["value"]))
            except ValueError:
                pass
    aliases = []
    for lang in ("zh", "en"):
        if lang in entity.get("labels", {}):
            aliases.append((entity["labels"][lang]["value"], lang))
        aliases.extend((row["value"], lang) for row in entity.get("aliases", {}).get(lang, []))
    return [{"formula": formula, "aliases": aliases, "source": "wikidata",
             "source_url": f"https://www.wikidata.org/wiki/{entity_id}"} for formula in set(formulas)]


def wikidata(query, timeout=6):
    response = requests.get("https://www.wikidata.org/w/api.php", headers=HEADERS, timeout=timeout,
                            params={"action": "wbsearchentities", "search": query, "language": "zh" if re.search(r"[\u4e00-\u9fff]", query) else "en", "limit": 3, "format": "json"})
    response.raise_for_status()
    rows = []
    for item in response.json().get("search", []):
        rows.extend(wikidata_entity(item["id"], timeout))
    return rows
