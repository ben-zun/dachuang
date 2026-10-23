"""Deterministic provenance checks, not a general scientific truth verifier."""
import json
import math
import re
from scipy.constants import atomic_mass

NUM=r'[-+−]?(?:\d+(?:\.\d*)?|\.\d+)(?:(?:[eE][-+]?\d+)|(?:\s*(?:×|\\times|\*)\s*10\s*(?:\^|\*\*)\s*\{?[-+−]?\d+\}?)|(?:\s*(?:\^|\*\*)\s*\{?[-+−]?\d+\}?)|[⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺]+)?'
UNIT=r'(?:meV/atom|eV/atom|eV/(?:fu|f\.u\.|formula\s*unit)|kJ/mol|J/mol|eV/K|J/K|C/mol|g/cm[³3]|kg/m[³3]|g/mol|kg/mol|eV/Å|eV/angstrom|amu/Å(?:³|3|\^3)|amu|Å(?:³|3|\^3)|cm(?:³|3|\^3)|m(?:³|3|\^3)|meV|eV|GPa|MPa|Pa|nm|μm|µm|um|mm|cm|angstrom|Å|kg|g|mol|mT|µT|μT|T|J|K|m|%)(?![A-Za-z])'
QUANTITY=re.compile(r'(?<![A-Za-z0-9_.-])(?P<value>'+NUM+r')\s*(?P<unit>'+UNIT+r')(?!/)')
NUMBER=re.compile(r'(?<![A-Za-z0-9_.-])'+NUM)
# (dimension/normalization, scale). eV and eV/atom are deliberately distinct.
UNITS={'eV':('energy',1.),'meV':('energy',.001),'J':('energy',1/1.602176634e-19),
 'eV/atom':('per_atom',1.),'meV/atom':('per_atom',.001),'eV/fu':('per_formula',1.),
 'kJ/mol':('per_atom',1000/(6.02214076e23*1.602176634e-19)),
 'J/mol':('per_atom',1/(6.02214076e23*1.602176634e-19)),
 'T':('magnetic_flux_density',1.),'mT':('magnetic_flux_density',.001),'µT':('magnetic_flux_density',1e-6),'μT':('magnetic_flux_density',1e-6),
 'K':('temperature',1.),'eV/K':('boltzmann',1.),'J/K':('boltzmann',1/1.602176634e-19),
 'C/mol':('faraday',1.),'g':('mass',1.),'kg':('mass',1000.),'g/mol':('molar_mass',1.),'kg/mol':('molar_mass',1000.),
 'm':('length',1.),'nm':('length',1e-9),'Å':('length',1e-10),'angstrom':('length',1e-10),
 'eV/Å':('force',1.),'eV/angstrom':('force',1.),'amu':('mass',atomic_mass*1000),'Å3':('volume',1e-30),'Å^3':('volume',1e-30),'cm3':('volume',1e-6),'cm^3':('volume',1e-6),'m3':('volume',1.),'m^3':('volume',1.),'amu/Å3':('density',atomic_mass*1e27),'amu/Å^3':('density',atomic_mass*1e27),
 'g/cm3':('density',1.),'kg/m3':('density',.001),'Pa':('pressure',1.),'MPa':('pressure',1e6),'GPa':('pressure',1e9),'%':('fraction',.01)}
UNITS.update({'um':('length',1e-6),'μm':('length',1e-6),'µm':('length',1e-6),'mm':('length',1e-3),'cm':('length',1e-2)})
CONSTANTS={'atomic_mass':{'value':atomic_mass,'unit':'kg','aliases':r'amu|原子质量|atomic mass'},'k_B':{'value':1.380649e-23,'unit':'J/K','aliases':r'k_?B|玻尔兹曼|Boltzmann'},
 'Faraday':{'value':96485.33212,'unit':'C/mol','aliases':r'Faraday|法拉第'},
 'N_A':{'value':6.02214076e23,'unit':'mol^-1','aliases':r'N_?A|阿伏伽德罗|Avogadro'},
 'electron_volt_J':{'value':1.602176634e-19,'unit':'J','aliases':r'eV|电子伏'}}
PROPERTIES={'band_gap':('eV',r'带隙|band[ _-]?gap'),
 'formation_energy_per_atom':('eV/atom',r'形成能|formation[ _]energy'),
 'energy_above_hull':('eV/atom',r'Ehull|E_hull|energy[ _]above[ _]hull|凸包'),
 'density':('g/cm3',r'密度|density'),'space_group_number':('',r'空间群编号|space.group.number')}

def unit(s):return re.sub(r'\s+','',s).replace('³','3').replace('eV/formulaunit','eV/fu').replace('eV/f.u.','eV/fu')
def value(s):
    s=s.replace('−','-').replace('\\times','×').replace('**','^').replace('{','').replace('}','').replace(' ','')
    supers='⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺'
    m=re.search('['+supers+']+$',s)
    if m:s=s[:m.start()]+'^'+m.group().translate(str.maketrans(supers,'0123456789-+'))
    if '×10^' in s:
        a,b=s.split('×10^');return float(a)*10**int(b)
    if '*10^' in s:
        a,b=s.split('*10^');return float(a)*10**int(b)
    if '^' in s:
        a,b=s.split('^');return float(a)**int(b)
    return float(s)

def equivalent(a,au,b,bu,tol=5e-5):
    au,bu=unit(au),unit(bu)
    if au==bu:return math.isclose(a,b,rel_tol=tol,abs_tol=1e-12)
    if au not in UNITS or bu not in UNITS or UNITS[au][0]!=UNITS[bu][0]:return False
    return math.isclose(a*UNITS[au][1],b*UNITS[bu][1],rel_tol=tol,abs_tol=1e-30)

def quantities(text):
    out=[]
    for m in QUANTITY.finditer(text):
        try:out.append({'value':value(m['value']),'unit':unit(m['unit']),'span':[m.start(),m.end()],'raw':m.group(),'value_text':m['value']})
        except (ValueError,OverflowError):pass
    return out

def display_matches(q,expected,expected_unit):
    if equivalent(q['value'],q['unit'],expected,expected_unit):return True
    raw=q.get('value_text','')
    if not q['value'] or not re.fullmatch(r'[-+]?\d+\.\d+',raw):return False
    decimals=len(raw.split('.')[1]);tolerance=.5*10**(-decimals)/abs(q['value'])
    return equivalent(q['value'],q['unit'],expected,expected_unit,tol=tolerance+1e-12)

def user_quantities(context):
    qs=quantities(context)
    # Bind a basis to its nearby input, never to a conversion target elsewhere
    # in the sentence ("每化学式-14 eV换成每原子能量"). Explicit /atom wins.
    for q in qs:
        if q['unit'] not in ('eV','meV'):continue
        prefix=context[max(0,q['span'][0]-45):q['span'][0]]
        markers=list(re.finditer(r'每原子|每(?:个)?化学式(?:单元)?|per[- ]atom|per formula(?: unit)?',prefix,re.I))
        if not markers:continue
        marker=markers[-1]
        if re.search(r'[。；;!?！？]',prefix[marker.end():]):continue
        if re.search(r'原子|atom',marker.group(),re.I):q.update(unit=q['unit']+'/atom',basis='explicit per-atom input')
        elif q['unit']=='eV':q.update(unit='eV/fu',basis='explicit per-formula input')
    if '分别' in context and len(qs)==1:
        shared=qs[0]
        for m in NUMBER.finditer(context.split('分别',1)[1].split('，')[0]):
            try:n=value(m.group())
            except (ValueError,OverflowError):continue
            if not any(math.isclose(n,q['value'],rel_tol=1e-12,abs_tol=0) for q in qs):qs.append({'value':n,'unit':shared['unit'],'raw':m.group(),'origin':'explicit enumeration shares following unit'})
    return qs

def material_records(materials):
    return [r for result in materials if result.get('status')=='ok' for r in result.get('candidates',[])]

def calculation_records(calculations):
    result=[]
    for i,c in enumerate(calculations):
        if c.get('status')!='ok':continue
        from backend.tools.calculation import run
        try:expected,_=run(c['operation'],c['inputs'])
        except (ValueError,TypeError,KeyError):continue
        valid=expected==c['result']
        result.append({'category':'calculated','calculation_id':i,'operation':c['operation'],'inputs':c['inputs'],
          'expression':c['inputs'].get('expression') or c.get('provenance',{}).get('expression'),
          'output':c['result'],'recomputed_output':expected,'verified':valid,
          'input_provenance':c.get('provenance',{}).get('inputs',[]),
          'quantities':[{'value':v,'unit':{'volume_angstrom3':'Å3','density_g_cm3':'g/cm3','molar_mass_g_mol':'g/mol','reaction_energy_eV':'eV'}.get(k,'')} for k,v in c['result'].items() if type(v) in (float,int) and k in ('volume_angstrom3','density_g_cm3','molar_mass_g_mol','reaction_energy_eV')]})
    return result

def collection_bindings(text,materials):
    """Check explicitly labelled aggregates against the complete returned candidate set.

    No inference of aggregation from coincidentally equal numbers or material rows.
    """
    sets=[r for r in materials if r.get('status')=='ok' and r.get('candidates')]
    if len(sets)!=1:return []
    result=sets[0];out=[]
    for sentence in re.finditer(r'[^。;；\n]+',text):
        t=sentence.group()
        if re.search(r'mp-\d+',t) or not re.search(r'本次|返回|候选|筛选|集合|returned|candidates',t,re.I):continue
        props=[k for k,(_,pattern) in PROPERTIES.items() if re.search(pattern,t,re.I)]
        if len(props)!=1:continue
        prop=props[0];vals=[r.get(prop) for r in result['candidates']]
        if any(type(v) not in (int,float) for v in vals):continue
        expected_unit=PROPERTIES[prop][0]
        pattern=r'(?:范围|range)[^0-9+−-]{0,12}(?P<lo>'+NUM+r')\s*(?:'+UNIT+r')?\s*(?:至|到|~|～|–|to)\s*(?P<hi>'+NUM+r')\s*(?P<u>'+UNIT+r')'
        m=re.search(pattern,t,re.I)
        if m:
            for key,expected in [('lo',min(vals)),('hi',max(vals))]:
                a,b=m.span(key);q={'value':value(m[key]),'value_text':m[key],'unit':unit(m['u'])}
                out.append({**q,'span':[sentence.start()+a,sentence.start()+b],'expected_value':expected,'expected_unit':expected_unit,'property':prop,'verified':display_matches(q,expected,expected_unit),'aggregation':'min' if key=='lo' else 'max','candidate_ids':[r['material_id'] for r in result['candidates']]})
        else:
            for q in quantities(t):
                before=t[:q['span'][0]]
                agg='min' if re.search(r'(?:最小|最低|minimum|min)[^。0-9]{0,12}$',before,re.I) else 'max' if re.search(r'(?:最大|最高|maximum|max)[^。0-9]{0,12}$',before,re.I) else None
                if agg:
                    expected=min(vals) if agg=='min' else max(vals)
                    out.append({**q,'span':[sentence.start()+x for x in q['span']],'expected_value':expected,'expected_unit':expected_unit,'property':prop,'verified':display_matches(q,expected,expected_unit),'aggregation':agg,'candidate_ids':[r['material_id'] for r in result['candidates']]})
    return out

def inspect_text(text,user_message='',sources=(),materials=(),calculations=(),source_fact=False):
    """Only replace an implicated quantity, never a sentence or whole paragraph."""
    given=user_quantities(user_message);records=[];events=[];replacements=[];rows=material_records(materials)
    calc=calculation_records(calculations)
    aggregates=collection_bindings(text,materials)
    for a in aggregates:
        records.append({**a,'category':'tool_result','verification':'returned_set_aggregate','status':'verified' if a['verified'] else 'flagged'})
        if not a['verified']:
            replacements.append((*a['span'],'（该集合统计数值未通过核验）'))
            events.append({'type':'collection_aggregate_mismatch','span':a['span'],'expected_value':a['expected_value'],'action':'replace_quantity_only'})
    for q in quantities(text):
        if any(a['span'][0]<=q['span'][0]<a['span'][1] for a in aggregates):continue
        start,end=q['span'];left=text[max(0,start-160):start]
        clause=re.split(r'[。！？;；\n]',left)[-1]
        ids=re.findall(r'(?<![A-Za-z0-9])mp-\d+(?!\d)',clause);identity=ids[-1] if ids else None
        props=[(m.start(),key) for key,(_,pattern) in PROPERTIES.items() for m in re.finditer(pattern,re.sub(r'（[^）]*）|\([^)]*\)','',clause),re.I)]
        prop=max(props)[1] if props else None
        row=next((r for r in rows if r.get('material_id')==identity),None) if identity else None
        primary_binding=False
        comparison=bool(re.search(r'(?:小于|低于|不高于|阈值|上限|<|≤)[^。;；]{0,35}$',clause,re.I))
        if identity is None and prop and not comparison:
            matches=[r for r in materials if r.get('status')=='ok' and r.get('primary') and not r.get('filters') and any(name and name in clause for name in (r.get('material_query'),r['primary'].get('formula')))]
            if len(matches)==1:
                row=matches[0]['primary'];identity=row['material_id'];primary_binding=True
        rec={**q,'category':'model_claim','categories':[],'material_id':identity,'property':prop,'verified':False,'primary_structure_binding':primary_binding}
        reason=None
        # Identity/attribute binding takes priority over a coincidentally equal user number.
        if identity and prop and rows and not comparison:
            if row is None:reason='unknown_material_binding'
            elif row.get(prop) is not None:
                expected=row[prop];expected_unit=PROPERTIES[prop][0]
                rec.update(category='tool_result',expected_value=expected,expected_unit=expected_unit)
                if display_matches(q,expected,expected_unit):rec.update(verified=True,verification='value_matches_with_display_rounding')
                else:reason='material_property_value_or_unit_mismatch'
        if not reason and not rec['verified']:
            same=[g for g in given if equivalent(q['value'],q['unit'],g['value'],g['unit'])]
            if same:rec.update(category='user_given',verified=True,origin=same[0])
            # Matching exact-span evidence is provenance, not proof of semantic entailment.
            for s in sources:
                evidence=s.get('support_quote') or s.get('text','')
                if s.get('admission_grade',s.get('permission')) not in ('fact-grade','fact'):
                    quote=s.get('support_quote','')
                    if not quote or not any(' '.join(quote.split()) in ' '.join(t.split()) for t in s.get('fact_texts',[])):continue
                    evidence=quote
                if any(display_matches(q,g['value'],g['unit']) for g in quantities(evidence)):
                    rec.update(category='tool_result',verified=True,source_id=s.get('source_id',s.get('number')),
                               verification='source_value_match_only',semantic_verified=False);break
            for c in calc:
                o=c['output'];u=o.get('unit','');v=o.get('value')
                if not c['verified']:continue
                known=([{'value':v,'unit':u}] if type(v) in (float,int) and u else [])+c['quantities']
                if any(display_matches(q,x['value'],x['unit']) for x in known):rec.update(category='calculated',verified=True,unit_verified=True,calculation_id=c['calculation_id'])
                elif c['operation']=='math' and type(v) in (float,int) and math.isclose(q['value'],v,rel_tol=5e-5,abs_tol=1e-12):
                    rec.update(category='calculated',verified=True,unit_verified=False,calculation_id=c['calculation_id'])
            for name,c in CONSTANTS.items():
                if not re.search(c['aliases'],clause,re.I):continue
                if equivalent(q['value'],q['unit'],c['value'],c['unit'],tol=1e-4):rec.update(category='physical_constant',verified=True,constant=name)
                elif name in ('k_B','Faraday'):reason='physical_constant_value_or_unit_mismatch'
        definition_math=(identity is None and prop=='energy_above_hull' and q['value']==0 and bool(re.search(r'意味着|表示|当|若|定义',clause+text[end:end+20])) and not re.search(r'实验|测得|报告|数据库|论文',clause))
        if definition_math:rec.update(category='math_expression',verified=True,verification='definition_boundary_only')
        local_clause=re.split(r'[，,]|\b(?:whereas|while|but)\b',clause,flags=re.I)[-1]
        if q['unit']=='K' and q['value']==0 and not re.search(r'实验|测得|报告|论文|measured|experiment',local_clause,re.I):rec.update(category='math_expression',verified=True,verification='zero_temperature_reference')
        specific=(source_fact and not definition_math) or bool(identity and prop) or bool(re.search(r'实验|测得|报告|报道|数据库|论文|文献|测试显示|measured|reported|database|experiment',clause,re.I))
        if not reason and specific and not rec['verified']:reason='unverified_specific_numeric_claim'
        rec['categories'].append(rec['category'])
        if reason:
            rec.update(verified=False,status='flagged',reason=reason)
            # Missing provenance is a review signal, not a license to change a
            # scientific sentence. Explicit receipt/value mismatches stay local.
            action='audit_only' if reason=='unverified_specific_numeric_claim' else 'replace_quantity_only'
            if action!='audit_only':replacements.append((start,end,'（该具体数值未通过核验）'))
            events.append({'type':reason,'span':[start,end],'original':q['raw'],'action':action,'material_id':identity,'property':prop})
        else:
            rec['status']='verified' if rec['verified'] else 'unverified_general_value'
            if not rec['verified'] and (sources or materials or calculations):
                events.append({'type':'numeric_value_not_verified','span':[start,end],
                               'original':q['raw'],'action':'audit_only'})
        records.append(rec)
    occupied=[r['span'] for r in records]
    for m in NUMBER.finditer(text):
        if any(a<=m.start()<b for a,b in occupied):continue
        before=text[:m.start()];after=text[m.end():]
        # Absence of a recognized unit is not evidence of a mathematical role.
        # Keep unknown quantities intact and explicitly unclassified.
        unknown=re.match(r'\s*(°[CF]|[A-Za-zµμΩ][A-Za-z0-9µμΩ^/·⁻¹²³+-]{0,24})(?![A-Za-z])',after)
        math_role=bool(re.search(r'(?:^|\n)\s*(?:#{1,6}\s*)?$',before) and re.match(r'[.)、]\s',after))
        math_role=math_role or bool(m.group().endswith('.') and re.search(r'(?:^|\n)\s*(?:#{1,6}\s*)?$',before))
        math_role=math_role or bool(before.count('$')%2 or before.rfind(r'\(')>before.rfind(r'\)') or before.rfind(r'\[')>before.rfind(r'\]'))
        math_role=math_role or bool(re.search(r'(?:\b[A-Za-z_]\w*\s*[=<>]|[\^_]\{?)\s*$',before))
        unknown_unit=unknown[1] if unknown and not math_role else None
        category='math_expression' if math_role else 'model_claim'
        records.append({'category':category,'categories':[category],'raw':m.group(),
                        'span':[m.start(),m.end()],'unit':unknown_unit,'verified':None,
                        'status':'preserved_not_truth_verified' if math_role else 'unknown_unit' if unknown_unit else 'unclassified_number'})
        if unknown_unit:
            events.append({'type':'unrecognized_numeric_unit','original':m.group()+unknown[0],
                           'span':[m.start(),m.end()+unknown.end()],'action':'audit_only'})
    for start,end,replacement in reversed(replacements):text=text[:start]+replacement+text[end:]
    return text,events,records


def validate_json_answer(text,materials,calculations):
    """Validate explicitly named JSON properties; preserve JSON syntax on failure."""
    try:payload=json.loads(text)
    except ValueError:return text,[],[]
    events=[];records=[];rows=material_records(materials)
    def walk(obj,parent_identity=None):
        if isinstance(obj,list):
            for x in obj:walk(x,parent_identity)
        if not isinstance(obj,dict):return
        identity=obj.get('material_id',parent_identity);row=next((r for r in rows if r.get('material_id')==identity),None)
        for prop,(u,_) in PROPERTIES.items():
            v=obj.get(prop)
            if type(v) not in (float,int):continue
            if not identity:continue
            valid=row is not None and row.get(prop) is not None and equivalent(v,obj.get('unit',u),row[prop],u)
            records.append({'category':'tool_result','material_id':identity,'property':prop,'value':v,'unit':obj.get('unit',u),'verified':valid})
            if not valid:
                obj[prop]=None;events.append({'type':'structured_binding_mismatch','material_id':identity,'property':prop,'original':v,'action':'set_only_invalid_value_null'})
        if type(obj.get('value')) in (float,int) and isinstance(obj.get('unit'),str):
            v=obj['value'];u=obj['unit'];expected=[]
            prop=obj.get('property')
            if row and prop in PROPERTIES and type(row.get(prop)) in (float,int):expected.append((row[prop],PROPERTIES[prop][0],'tool_result'))
            for c in calculation_records(calculations):
                if not c['verified']:continue
                result=c['output']
                if type(result.get('value')) in (float,int) and result.get('unit'):expected.append((result['value'],result['unit'],'calculated'))
            if len(expected)==1:
                ev,eu,category=expected[0];valid=equivalent(v,u,ev,eu)
                records.append({'category':category,'value':v,'unit':u,'expected_value':ev,'expected_unit':eu,'verified':valid})
                if not valid:obj['value']=None;events.append({'type':'structured_value_unit_mismatch','original':v,'action':'set_only_invalid_value_null'})
        for v in obj.values():walk(v,identity)
    walk(payload)
    return (json.dumps(payload,ensure_ascii=False) if events else text),events,records


def validate_tool_result(call,result):
    """Catch deterministic result inconsistencies before they reach display/context."""
    if result.get('status') not in ('ok','success'):return result,[]
    reason=None
    if call['name']=='calculate':
        checked=calculation_records([result])
        if not checked or not checked[0]['verified']:reason='calculation_output_mismatch'
    elif call['name']=='search_materials':
        query=call['arguments'].get('material_query','').strip()
        if re.fullmatch(r'mp-\d+',query):
            if any(r.get('material_id')!=query for r in result.get('candidates',[])):reason='query_material_binding_mismatch'
            if result.get('primary') and result['primary'].get('material_id')!=query:reason='query_material_binding_mismatch'
    if not reason:return result,[]
    event={'type':reason,'tool':call['name'],'action':'reject_inconsistent_tool_record','task_id':call['id']}
    return {**result,'status':'error','message':'工具结果未通过对象或计算一致性检查；该具体结果暂不展示。'},[event]


def format_calculated_box(answer,contract,calculations):
    """Box an explicitly concluded, recomputed result; never select a middle number."""
    if not contract.boxed or r'\boxed{' in answer:return answer
    checked=[c for c in calculation_records(calculations) if c['verified'] and type(c['output'].get('value')) in (int,float)]
    if len(checked)!=1:return answer
    m=re.search(r'(?:最终答案|答案|结果|能量)(?:为|是|=)\s*(?P<value>'+NUM+r')\s*(?:'+UNIT+r')?[。.]?\s*$',answer)
    if not m:return answer
    try:matches=math.isclose(value(m['value']),checked[0]['output']['value'],rel_tol=5e-5,abs_tol=1e-12)
    except (ValueError,OverflowError):return answer
    if not matches:return answer
    a,b=m.span('value');return answer[:a]+r'\boxed{'+m['value']+'}'+answer[b:]
