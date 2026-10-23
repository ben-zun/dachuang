"""Backend-owned conversation receipts. Assistant prose never becomes a fact."""
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from uuid import uuid4

from backend.paths import ROOT

LABELS={
 'mp-390':{'name':'anatase','zh':'锐钛矿','formula':'TiO2','url':'https://materialsproject.org/materials/mp-390/'},
 'mp-2657':{'name':'rutile','zh':'金红石','formula':'TiO2','url':'https://materialsproject.org/materials/mp-2657/'}}

def identity(row):
    row=deepcopy(row);label=LABELS.get(row.get('material_id'))
    row['structure_label']=label['name']+' / '+label['zh'] if label and row.get('formula')==label['formula'] else ' / '.join(str(row[k]) for k in ('space_group','crystal_system') if row.get(k))
    row['structure_label_source']='local_curated_mapping; reference='+label['url'] if label and row.get('formula')==label['formula'] else 'local_display_derived_from_MP_space_group_crystal_system'
    return row

def object_text(message):
    # A negated switch is not a new selection ("不要改成mp-149").
    return re.sub(r'(?:不要|(?<!分)别|不必|无需|不是|并非)\s*(?:改成|改为|改回|切换到|切回|换回|回到|沿用|查询|查|用)?\s*(?:mp-\d+|(?:[A-Z][a-z]?\d*)+)', '', message)

def normalize_material_text(message):
    # Join layout-only line breaks inside a formula, never arbitrary word spaces.
    return re.sub(r'((?:[A-Z][a-z]?\d*)*[A-Z][a-z]?)\s*\n\s*(?=\d)', r'\1', message)


def same_material(left, right):
    if left == right: return True
    if not isinstance(left, str) or not isinstance(right, str): return False
    from backend.materials.resolver import resolve_material
    a, b = resolve_material(normalize_material_text(left)), resolve_material(normalize_material_text(right))
    if a['kind'] != b['kind']: return False
    if a['kind'] == 'formula':
        from pymatgen.core import Composition
        return Composition(a['value']).fractional_composition == Composition(b['value']).fractional_composition
    return a['kind'] != 'unresolved' and a.get('value') == b.get('value')


def object_formula(value):
    from backend.materials.resolver import resolve_material
    resolved=resolve_material(value)
    return resolved.get('value') if resolved['kind']=='formula' else value


def lowest_ehull_requested(message):
    return bool(re.search(r'最低(?:的)?\s*Ehull|(?:选|按|以).{0,25}Ehull.{0,8}最低.{0,15}(?:主结果|为主|为准)|选.{0,25}Ehull.{0,8}最低(?:的)?[。.]?$',message,re.I))


def explicit_objects(message):
    """Only IDs, syntax-checked formulae and local aliases; no model inference."""
    message=object_text(normalize_material_text(message))
    named=[mid for mid,label in LABELS.items() if re.search(r'(?<![A-Za-z])'+label['name']+r'(?![A-Za-z])',message,re.I) or label['zh'] in message]
    ids=re.findall(r'(?<![A-Za-z0-9_])mp-\d+(?!\d)',message)
    if ids:return list(dict.fromkeys(ids))
    if named:return named
    from backend.materials.query_syntax import without_space_groups
    message=without_space_groups(message)
    original=message
    from backend.materials.chemical_names import binary_name
    compositional=[]
    for m in re.finditer(r'(?<![A-Za-z])[A-Za-z]+(?:\s*\([IVXivx]+\))?\s+(?:oxide|fluoride|chloride|bromide|iodide|nitride|sulfide)(?![A-Za-z])',message):
        f=binary_name(m.group())
        if f:compositional.append(f)
    from backend.materials.phases import mentions
    qualified,message=mentions(message)
    from backend.materials.resolver import resolve_material
    candidates=re.findall(r'(?<![A-Za-z])(?:[A-Z][a-z]?\d*)+(?![A-Za-z])',message)
    alias_path=ROOT/'data/material_aliases.json'
    aliases=set(json.loads(alias_path.read_text())) if alias_path.exists() else set()
    from backend.materials.aliases import connect
    with connect() as db:
        aliases.update(r[0] for r in db.execute('SELECT DISTINCT alias FROM aliases'))
    if aliases:
        remaining=message
        for alias in sorted(aliases, key=len, reverse=True):
            pattern=(r'(?<![A-Za-z])'+re.escape(alias)+r'(?![A-Za-z])') if re.search(r'[A-Za-z]',alias) else re.escape(alias)
            matches=list(re.finditer(pattern,remaining,re.I))
            if matches:
                if len(alias)==1:
                    # An element mentioned inside a family adjective (high-Ni,
                    # Li-rich, doped-Si) is not an elemental-material query.
                    hits=matches
                    if not any(m.start()==0 or not re.match(r'[\u4e00-\u9fff]',remaining[m.start()-1])
                               or remaining[m.start()-1] in '查询与和及比对' for m in hits):
                        continue
                candidates.append(alias)
                remaining=re.sub(pattern,' ',remaining,flags=re.I)
    out=list(dict.fromkeys(qualified+[f for f in compositional if not any(object_formula(q)==f for q in qualified)]))
    positions={q:original.casefold().find(q.casefold()) for q in qualified}
    for q in candidates:
        try:
            # Composition accepts isotope symbols (e.g. ID -> HI). A token
            # extracted from prose must use literal element symbols first;
            # fuzzy name/typo repair belongs to the explicit resolver path.
            if re.fullmatch(r'(?:[A-Z][a-z]?\d*)+', q):
                from pymatgen.core import Element
                if any(Element(symbol).symbol!=symbol for symbol in re.findall(r'[A-Z][a-z]?', q)):
                    continue
            r=resolve_material(q)
            if r['kind'] in ('formula','material_id'):
                value=r['value'];pos=original.casefold().find(q.casefold())
                if value not in out:out.append(value)
                if pos>=0:positions[value]=min(positions.get(value,pos),pos)
        except (ValueError,TypeError):pass
    return sorted(out,key=lambda value:positions.get(value,len(original)))

class SessionState:
    def __init__(self,data=None):
        self.data=deepcopy(data) if data else dict(history=[],active=None,candidates=[],mp_records=[],calc_records=[],user_records=[],counter=0,calc_counter=0,last_ui_id=None)
        self.current='';self.explicit=[]
    def next_id(self,prefix):
        self.data['counter']+=1;return prefix+'_'+str(self.data['counter'])
    def begin(self,message,selected=None):
        self.current=message;self.explicit=explicit_objects(message)
        if self.explicit:self.data['requested_objects']=self.explicit[:8]
        ui_id=selected.get('material_id') if selected else None
        if ui_id!=self.data['last_ui_id']:
            if selected:self.accept_mp({'status':'ok','candidates':[selected]},selection=ui_id)
            elif self.data['last_ui_id']:self.data['active']=None;self.data['candidates']=[]
        self.data['last_ui_id']=ui_id
        ranked=self.data.get('last_ranked_material_ids',[])
        from backend.materials.query_syntax import ordinal, space_groups, norm_group
        position=ordinal(message) if re.search(r'上轮|上一轮|刚才',message) else None
        if not self.explicit and ranked and position and (position==-1 or 1<=position<=len(ranked)):
            mid=ranked[-1 if position==-1 else position-1]
            self.data['active']=next((deepcopy(r['data']) for r in reversed(self.data['mp_records']) if r['data']['material_id']==mid),None)
        groups=space_groups(message)
        if not self.explicit and ranked and len(groups)==1 and re.search(r'上轮|上一轮|刚才',message):
            rows={r['data']['material_id']:r['data'] for r in self.data['mp_records'] if r['data']['material_id'] in ranked and norm_group(str(r['data'].get('space_group','')))==norm_group(groups[0])}
            if len(rows)==1:self.data['active']=deepcopy(next(iter(rows.values())))
        active=self.data['active']
        if self.explicit and active and not any(x in (active['material_id'],active['formula']) for x in self.explicit):
            self.data['active']=None;self.data['candidates']=[]
        from backend.agent.numeric_validation import user_quantities
        for q in user_quantities(message):
            self.data['user_records'].append(dict(ref=self.next_id('user')+'.value',value=q['value'],unit=q['unit'],
                object=self.explicit[0] if len(self.explicit)==1 else (self.data['active'] or {}).get('material_id'),
                formula=self.formula(),property='user_given_quantity',basis='per_atom' if q['unit']=='eV/atom' else 'explicit_unit',
                source='user_input',source_type='user_given',text=message))
    def formula(self):
        if len(self.explicit)==1 and not self.explicit[0].startswith('mp-'):return object_formula(self.explicit[0])
        return (self.data['active'] or {}).get('formula')
    def relevant(self,row):
        obj=row.get('object') or row.get('material_id');formula=row.get('formula')
        active=self.data['active']
        if active and re.search(r'同一条目|该条目|它的|刚才(?:选中|选定)',self.current) and (not self.explicit or any(same_material(x,active['formula']) or x==active['material_id'] for x in self.explicit)):
            return not obj or obj in (active['material_id'],active['formula'])
        if self.explicit:return not obj or obj in self.explicit or formula in [object_formula(x) for x in self.explicit]
        if re.search(r'分别|比较|这些|这[两三几]个|刚才(?:的)?[两三]个|each|compare',self.current,re.I):
            return not obj or any(obj in (r['data']['material_id'],r['data']['formula']) for r in self.data['mp_records'][-8:])
        active=self.data['active']
        return not active or not obj or obj in (active['material_id'],active['formula'])
    def materials(self):
        rows=[r['data'] for r in self.data['mp_records'] if self.relevant(r['data'])]
        return [{'status':'ok','candidates':rows}] if rows else []
    def quantities(self):
        from backend.agent.facts import UNITS
        out=[deepcopy(r) for r in self.data['user_records'] if self.relevant(r)]
        for r in self.data['mp_records']:
            row=r['data']
            if not self.relevant(row):continue
            for k,u in UNITS.items():
                if type(row.get(k)) in (int,float):out.append(dict(ref=r['record_id']+'.'+k,object=row['material_id'],formula=row['formula'],property=k,value=row[k],unit=u,basis='per_atom' if u=='eV/atom' else 'reported',source='materials_project',source_type='tool_fact'))
        for r in self.data['calc_records']:
            out += [deepcopy(q) for q in r.get('quantities',[]) if self.relevant(q)]
        return out[-96:]
    def unspecified_energy_refs(self):
        refs=[]
        for q in self.data['user_records']:
            if q['unit'] not in ('eV/atom','eV/fu','meV/atom'):continue
            if not self.relevant(q):continue
            text=re.sub(r'(?:不是|并非|不代表|不要(?:称为|当成|定义为)?)\s*(?:总能|形成能|total energy|formation energy|Ehull)', '', q.get('text',''),flags=re.I)
            if re.search(r'形成能|formation energy|总能|total energy|Ehull|above hull',text,re.I):refs=[]
            else:refs.append(q['ref'])
        for r in self.data['calc_records']:
            for q in r.get('quantities',[]):
                if q.get('unit') in ('eV/atom','eV/fu','meV/atom') and set(q.get('parents',[]))&set(refs):refs.append(q['ref'])
        return refs
    def authorize(self,query):
        from backend.materials.resolver import resolve_material
        resolved=resolve_material(query);v=resolved.get('value',query)
        if self.explicit:
            return any(same_material(v, obj) for obj in self.explicit) or query in object_text(normalize_material_text(self.current))
        if query in object_text(self.current):return True
        active=self.data['active']
        if re.search(r'分别|比较|这些|这[两三几]个|刚才(?:的)?[两三]个|each|compare',self.current,re.I):
            return any(v in (r['data']['material_id'],r['data']['formula']) for r in self.data['mp_records'][-8:])
        if active:return v in (active['material_id'],active['formula'])
        pending=self.data.get('requested_objects',[])
        return not self.data['candidates'] and len(pending)==1 and v in pending
    def accept_mp(self,result,selection=None):
        if result.get('status') not in ('ok','success'):return
        rows=[identity(r) for r in result.get('candidates',[])];result['candidates']=rows
        if result.get('primary'):result['primary']=next((r for r in rows if r['material_id']==result['primary']['material_id']),identity(result['primary']))
        for row in rows[:32]:
            if not any(r['data']==row for r in self.data['mp_records']):self.data['mp_records'].append(dict(record_id=self.next_id('mp'),status='success',source='materials_project',fetched_at=result.get('fetched_at') or time.time(),data=deepcopy(row)))
        comparing=bool(re.search(r'分别|比较|这些|这[两三几]个|刚才(?:的)?[两三]个|each|compare',self.current,re.I))
        self.data['candidates']=list(dict.fromkeys((self.data['candidates'] if comparing else [])+[r['material_id'] for r in rows[:32]]))[-32:]
        # A primary search ranking is not a user selection.
        if comparing:self.data['active']=None
        elif len(rows)==1:self.data['active']=deepcopy(rows[0])
        elif selection:self.data['active']=next((deepcopy(r) for r in rows if r['material_id']==selection),None)
        elif lowest_ehull_requested(self.current):
            energies=[r['energy_above_hull'] for r in rows if isinstance(r.get('energy_above_hull'),(float,int))]
            lowest=[r for r in rows if energies and r.get('energy_above_hull')==min(energies)]
            # A ranking alone is not selection; an explicit user criterion is.
            self.data['active']=deepcopy(lowest[0]) if len(lowest)==1 else None
        else:self.data['active']=None
    def accept_calc(self,result):
        if result.get('status')=='ok' and result.get('execution',{}).get('success'):
            result=deepcopy(result)
            result['user_context']=self.current
            self.data['calc_records'].append(result)
    def prompt(self):
        energy_refs=[q['ref'] for q in self.quantities() if q.get('unit') in ('eV/atom','eV/fu','meV/atom') and q.get('property') not in ('formation_energy_per_atom','energy_above_hull') and not re.search(r'形成能|formation energy|总能|total energy|Ehull|above hull',q.get('text',''),re.I)]
        facts=dict(energy_reference_not_defined=energy_refs,requested_objects=self.data.get('requested_objects',[]),last_tool_results=self.data.get('last_tool_results',[]),active_material=self.data['active'],candidate_ids=self.data['candidates'],quantities=[{k:q.get(k) for k in ('ref','object','formula','property','value','unit','basis','source','source_type')} for q in self.quantities()[-20:]])
        if not any(facts.values()):return ''
        return ('\n历史数值只继承其原始定义和单位；单位转换不能把未定义能量认定为总能、形成能或Ehull。'
                '\n数据库身份字段保持后台原文，执行状态以真实回执为准。MP编号不要求固定数字位数；timeout/error/ambiguous不是不存在的证明。')+'\n后端已确认会话状态（用户值为题设，非数据库事实；仅引用列出的ref，禁止猜result编号）：'+json.dumps(facts,ensure_ascii=False)
    def finish(self,message,answer,calc_counter):
        self.data['history'].extend([{'role':'user','content':message},{'role':'assistant','content':answer[:10000]}])
        self.data['history']=self.data['history'][-10:]
        while sum(len(x['content']) for x in self.data['history'])>35000:self.data['history']=self.data['history'][2:]
        self.data['mp_records']=self.data['mp_records'][-32:]
        self.data['calc_records']=self.data['calc_records'][-24:]
        self.data['user_records']=self.data['user_records'][-32:]
        self.data['calc_counter']=calc_counter

@contextmanager
def open_session(session_id=None):
    if session_id is not None and not re.fullmatch('[a-f0-9]{32}',session_id):raise ValueError('无效会话编号')
    directory=ROOT/'data/chat_sessions';directory.mkdir(parents=True,exist_ok=True)
    ttl=int(os.getenv('CHAT_SESSION_TTL_SECONDS', '0'))
    new=session_id is None;sid=session_id or uuid4().hex
    with (directory/(sid+'.lock')).open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        with sqlite3.connect(directory/'sessions.sqlite') as db:
            db.execute('CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, updated REAL, data TEXT)')
            row=db.execute('SELECT updated,data FROM sessions WHERE id=?',(sid,)).fetchone()
        if not new and (not row or (ttl>0 and time.time()-row[0]>ttl)):raise ValueError('会话已过期，请清除对话后重试')
        state=SessionState(json.loads(row[1]) if row else None)
        yield sid,state
        with sqlite3.connect(directory/'sessions.sqlite') as db:
            db.execute('INSERT OR REPLACE INTO sessions VALUES (?,?,?)',(sid,time.time(),json.dumps(state.data,ensure_ascii=False)))
            if ttl>0:db.execute('DELETE FROM sessions WHERE updated<?',(time.time()-ttl,))
        fcntl.flock(lock,fcntl.LOCK_UN)
