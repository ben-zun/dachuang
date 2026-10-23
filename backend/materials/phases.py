"""Sourced name/phase constraints; never store MP IDs or property values here.

Space-group matching is a candidate screen, not a full prototype proof.
"""
from functools import lru_cache
import json
import re
import unicodedata
from pathlib import Path


@lru_cache(maxsize=1)
def registry():
    return json.loads((Path(__file__).resolve().parents[2]/'data/material_phases.json').read_text())


def norm(text):
    return unicodedata.normalize('NFKC',text).strip()


def constraint(entry):
    return {k:entry[k] for k in ('name','space_group_numbers','source_url')}


def resolve_phase(query, resolve_base, modifiers=True):
    query=norm(query)
    for entry in registry():
        if query.casefold() in [norm(a).casefold() for a in entry.get('aliases',[])]:
            return dict(kind='formula',value=entry['formula'],source='curated_name_rule',source_url=entry['source_url'],**({'phase':constraint(entry)} if entry.get('space_group_numbers') else {}))
        if not modifiers:continue
        for modifier in entry.get('modifiers',[]):
            pattern=re.escape(modifier)
            if re.search(r'[A-Za-z]',modifier):pattern=r'(?<![A-Za-z])'+pattern+r'(?![A-Za-z])'
            if not re.search(pattern,query,re.I):continue
            base=re.sub(pattern,'',query,flags=re.I).strip(' -:()（）')
            if not base:continue
            resolved=resolve_base(base)
            if resolved['kind']=='formula' and not resolved.get('requires_database_verification'):
                return {**resolved,'source':'composition_with_phase_constraint','phase':constraint(entry)}
    return None


def mentions(text):
    """Keep original phase-qualified spans before ordinary alias extraction."""
    from backend.materials.aliases import connect
    from backend.materials.resolver import resolve_material
    text=norm(text)
    spans=[]
    for entry in registry():
        for alias in sorted(entry.get('aliases',[]),key=len,reverse=True):
            pattern=re.escape(norm(alias))
            if re.search(r'[A-Za-z]',alias):pattern=r'(?<![A-Za-z])'+pattern+r'(?![A-Za-z])'
            for m in re.finditer(pattern,text,re.I):
                if text[m.end():].startswith('型'):continue
                spans.append((m.start(),m.end()))
    with connect() as db:
        names={r[0] for r in db.execute('SELECT DISTINCT alias FROM aliases')}
    names.update(a for entry in registry() for a in entry.get('aliases',[]))
    bases=[]
    for name in sorted(names,key=len,reverse=True):
        pattern=re.escape(norm(name))
        if re.search(r'[A-Za-z]',name):pattern=r'(?<![A-Za-z])'+pattern+r'(?![A-Za-z])'
        bases.extend((m.start(),m.end()) for m in re.finditer(pattern,text,re.I))
    bases.extend((m.start(),m.end()) for m in re.finditer(r'(?<![A-Za-z])(?:[A-Z][a-z]?\d*)+(?![A-Za-z])',text))
    for entry in registry():
        for modifier in entry.get('modifiers',[]):
            for start,end in bases:
                prefix=re.search(re.escape(modifier)+r'\s*[-:]?\s*$',text[:start],re.I)
                suffix=re.match(r'\s*[(（]?'+re.escape(modifier)+r'[)）]?',text[end:],re.I)
                span=(prefix.start(),end) if prefix else (start,end+suffix.end()) if suffix else None
                if span and resolve_material(text[span[0]:span[1]]).get('phase'):spans.append(span)
    # Longest non-overlapping mentions own their complete expression.
    selected=[]
    for start,end in sorted(set(spans),key=lambda s:-(s[1]-s[0])):
        if not any(start<e and end>s for s,e in selected):selected.append((start,end))
    selected.sort()
    remaining=text
    for start,end in reversed(selected):remaining=remaining[:start]+' '*(end-start)+remaining[end:]
    return [text[s:e] for s,e in selected],remaining


def filter_candidates(rows, phase):
    allowed=set(phase['space_group_numbers'])
    return [r for r in rows if r.get('space_group_number') in allowed]
