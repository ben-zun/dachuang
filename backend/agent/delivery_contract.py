"""Explicit user scope and receipt-owned delivery. No model or retrieval calls."""
from copy import deepcopy
import json
import re

PROPERTY_TERMS = {
    'band_gap': r'带隙|band[_ ]?gap',
    'formation_energy_per_atom': r'形成能|formation[_ ]energy(?:_per_atom)?',
    'energy_above_hull': r'Ehull|凸包距离|energy[_ ]above[_ ]hull',
    'density': r'密度|density',
    'space_group': r'空间群|space[_ ]group',
    'crystal_system': r'晶系|crystal[_ ]system',
}


def user_scope(message):
    from backend.agent.session_state import explicit_objects, LABELS
    # Negative clauses cannot introduce a selection or a requested property.
    positive = re.sub(r'(?:不要|(?<!分)别|不必|无需|勿)(?!只)[^，。；;]*', '', message)
    systems = re.findall(r'(?<![A-Za-z])(?:[A-Z][a-z]?[-–])+(?:[A-Z][a-z]?)(?![A-Za-z])', positive)
    objects = explicit_objects(positive)
    if systems:
        remainder = positive
        for s in systems: remainder = remainder.replace(s, '')
        objects = systems + explicit_objects(remainder)
    # explicit_objects prioritizes IDs; recover separately written formulae too.
    if re.search(r'mp-\d+', positive):
        objects += explicit_objects(re.sub(r'mp-\d+', '', positive))
    objects = list(dict.fromkeys(objects))
    props = [p for p, pattern in PROPERTY_TERMS.items() if re.search(pattern, positive, re.I)]
    if re.search(r'晶体结构|不同结构|各(?:种|个)结构|多个结构', positive):
        props = list(dict.fromkeys(props + ['space_group', 'crystal_system']))
    filters = {}
    for prop, pattern in PROPERTY_TERMS.items():
        if prop not in ('band_gap', 'formation_energy_per_atom', 'energy_above_hull', 'density'): continue
        m = re.search('(?:'+pattern+r')\s*(<=|>=|<|>|≤|≥|不超过|不低于|至少|至多|小于|低于|大于|高于)\s*(-?\d+(?:\.\d+)?)\s*(meV|eV)?', positive, re.I)
        if m:
            op = {'<':'lt','>':'gt','<=':'lte','>=':'gte','≤':'lte','≥':'gte','不超过':'lte','不低于':'gte','至少':'gte','至多':'lte','小于':'lt','低于':'lt','大于':'gt','高于':'gt'}[m[1]]
            value = float(m[2]) * (.001 if (m[3] or '').lower() == 'mev' else 1)
            filters[prop] = {op: value}
    explanations = [part.strip(' ，。；;') for part in re.split(r'并且|并|同时|另外|而且|；|;', message)
                    if re.search(r'解释|说明.*(?:原因|区别|关系)|为什么', part)]
    if re.search(r'比较|对比|选型|选材|推荐|建议|优先|如何选择',positive):
        explanations.append(message)
    from backend.materials.query_syntax import integer, space_groups
    limit=re.search(r'(?:最多|至多|前)\s*([一二三四五六七八九十两0-9]+)\s*(?:条|项|个|候选|结构)',positive)
    limit_value=integer(limit[1]) if limit else None
    from backend.agent.session_state import lowest_ehull_requested
    if limit_value is None and lowest_ehull_requested(positive):limit_value=1
    groups=space_groups(positive)
    return dict(materials=objects, properties=props, filters=filters, must_cover_all=True,
                explanation='；'.join(explanations), overflow=len(objects)>8,
                max_rows=min(32,max(1,limit_value)) if limit_value else None,
                space_group=groups[0] if len(groups)==1 else None,
                sort_by_ehull=bool(re.search(r'Ehull|energy[_ ]above[_ ]hull|凸包距离',positive,re.I) and re.search(r'排序|升序|从低到高|最低|低Ehull',positive,re.I)),
                extraction='explicit_syntax_and_local_aliases_only', original=message)



def explicit_lookup(scope):
    """Only a user's explicit positive request can require a DB execution."""
    text=re.sub(r"(?:不要|不必|无需|不用|不查|别查|do not|don't|without)[^，。；;!?\n]*",'',scope.get('original',''),flags=re.I)
    return bool(scope.get('materials') and (scope.get('properties') or scope.get('filters'))
                and re.search(r'查询|(?<!检)查|筛选|筛出|检索数据库|search|look\s*up',text,re.I))

def inherit_scope(scope, previous):
    """Carry user constraints only for an explicit continuation of the same search."""
    if not previous or not re.search(r'仍|上轮|上一轮|原来|再增加|继续筛',scope.get('original','')):
        return scope
    # "the first row from last time" selects a structure; it does not repeat
    # a broad formula search. Only explicit filter continuation inherits scope.
    if not scope['filters'] and not re.search(r'范围仍|继续筛|保留.*条件|原来.*条件',scope.get('original','')):
        return scope
    if scope['materials'] and scope['materials']!=previous['materials']:return scope
    scope=deepcopy(scope)
    scope['materials']=scope['materials'] or previous['materials']
    inherited=deepcopy(previous['filters'])
    for prop,bounds in scope['filters'].items():inherited.setdefault(prop,{}).update(bounds)
    scope['filters']=inherited
    scope['properties']=list(dict.fromkeys(previous['properties']+scope['properties']))
    scope['max_rows']=scope.get('max_rows') or previous.get('max_rows')
    scope['sort_by_ehull']=scope.get('sort_by_ehull') or previous.get('sort_by_ehull',False)
    return scope


def bind_calls(calls, scope, records):
    """Expand a proposed DB call to every explicit object, before execution.

    Preserve the proposed arguments for audit. No model-created phase or filter
    can replace an explicit scope. Unknown scopes keep the old authorization.
    """
    if not scope['materials']: return calls
    out=[]; expanded=False
    previous = {r['arguments'].get('material_query') for r in records if r['name']=='search_materials'}
    for call in calls:
        if call['name'] != 'search_materials': out.append(call); continue
        if expanded: continue
        expanded=True
        if scope['overflow']:
            out.append({**call, 'scope_error':'Too many explicit objects; please split the query'})
            continue
        for i, obj in enumerate(scope['materials']):
            if obj in previous: continue
            args=deepcopy(call['arguments'])
            args['material_query']=obj
            args['properties']=list(dict.fromkeys(args.get('properties', [])+scope['properties']))
            if scope['filters']: args['filters']=deepcopy(scope['filters'])
            out.append({**call, 'id':call['id']+'-scope-'+str(i),
                        'requested_arguments':deepcopy(call['arguments']), 'arguments':args,
                        'scope_binding':'explicit_user_scope','space_group_constraint':scope.get('space_group')})
    return out


def check_result_scope(call, result):
    if call['name']!='search_materials' or result.get('status') not in ('ok','success'):return result,[]
    from backend.materials.resolver import resolve_material
    from backend.agent.session_state import same_material
    from pymatgen.core import Composition
    query=call['arguments'].get('material_query',''); resolved=resolve_material(query)
    group=call.get('space_group_constraint')
    if group:
        from backend.materials.query_syntax import norm_group
        result=deepcopy(result)
        result['candidates']=[r for r in result.get('candidates',[]) if norm_group(str(r.get('space_group','')))==norm_group(group)]
        result['primary']=result['candidates'][0] if result['candidates'] else None
        result['multiple_structures']=len(result['candidates'])>1
        result['applied_space_group_constraint']=group
        if not result['candidates']:result.update(status='no_database_record',message='当前回执中没有满足用户空间群约束的结构。')
    invalid=[]
    for row in result.get('candidates',[]):
        if resolved['kind']=='material_id': valid=row.get('material_id')==resolved['value']
        elif resolved['kind']=='formula': valid=same_material(row.get('formula',''),resolved['value'])
        elif resolved['kind']=='chemsys':
            try:valid=set(Composition(row.get('formula','')).as_dict())==set(resolved['value'].split('-'))
            except (ValueError,TypeError):valid=False
        else:valid=True  # Unknown names retain the resolver's existing contract.
        if valid and resolved.get('phase'):
            valid=row.get('space_group_number') in resolved['phase']['space_group_numbers']
        if not valid:invalid.append(row.get('material_id'))
    if not invalid:return result,[]
    event=dict(type='receipt_entity_scope_mismatch',action='reject_receipt',query=query,rejected_entities=invalid)
    return dict(status='error',message='工具回执对象不符合已确认查询范围，未用于交付。',candidates=[],rejected_result=deepcopy(result)),[event]


def delivery_report(scope, records):
    tasks=[]; bindings=[]
    lookup = bool(re.search(r'查询|查|对比|比较|筛选|筛出|给出|列出', scope.get('original','')))
    requested = any(r['name']=='search_materials' for r in records) or (lookup and bool(scope['properties']))
    for obj in scope['materials'] if requested else []:
        matching=[r for r in records if r['name']=='search_materials' and r['arguments'].get('material_query')==obj]
        ok=[r for r in matching if r['result'].get('status') in ('ok','success') and r['result'].get('candidates')]
        missing=[]
        for prop in scope['properties']:
            if not any(any(row.get(prop) is not None for row in r['result'].get('candidates',[])) for r in ok): missing.append(prop)
        state='SUCCESS' if ok and not missing else 'PARTIAL' if ok else 'UNRESOLVED'
        tasks.append(dict(subtask_id='material:'+obj,entity=obj,state=state,missing_fields=missing,
                          receipts=[r['evidence_id'] for r in matching]))
    for r in records:
        if r['name']=='calculate' and r['result'].get('execution',{}).get('success') and r['result'].get('status')=='ok':
            bindings.append(dict(receipt_id=r['evidence_id'],subtask_id='calculate:'+r.get('id',r['evidence_id']),
                                 entity_id=r['result'].get('inputs',{}).get('formula'),
                                 operation=r['result'].get('operation'),fields=deepcopy(r['result'].get('result',{})),
                                 quantities=deepcopy(r['result'].get('quantities',[]))))
        if r['name']!='search_materials' or r['result'].get('status') not in ('ok','success'):continue
        for row in r['result'].get('candidates',[]):
            bindings.append(dict(receipt_id=r['evidence_id'],subtask_id='material:'+r['arguments'].get('material_query',''),
                                 entity_id=row['material_id'],formula=row.get('formula'),
                                 fields={p:row[p] for p in PROPERTY_TERMS if row.get(p) is not None}))
    states=[t['state'] for t in tasks]
    state='NOT_REQUESTED' if not tasks else 'SUCCESS' if all(s=='SUCCESS' for s in states) else 'PARTIAL' if any(s in ('SUCCESS','PARTIAL') for s in states) else 'UNRESOLVED'
    return dict(scope=scope,subtasks=tasks,material_target_state=state,receipt_bindings=bindings,
                semantic_entailment_checked=False)


def guard_failed_claims(text, report, user_message):
    """Narrow result delivery guard, not a scientific verifier.

    Only result-bearing clauses naming a failed property with its dimensional
    value are replaced. No paragraph deletion, no formula/negative rewriting.
    User quantities are retained as user input. Every replacement is audited.
    """
    from backend.agent.numeric_validation import quantities, display_matches
    from backend.agent.facts import UNITS
    failed=[t for t in report['subtasks'] if t['state']!='SUCCESS']
    missing = set(p for t in failed for p in t['missing_fields'])
    events=[]; given=quantities(user_message)
    if not missing:return text,events
    def clause(m):
        s=m[0]
        # A model can rename a failed ID using remembered aliases. Thus the
        # guard covers unsolicited values for the unresolved property, even in
        # an added material example; successful data remains in receipt tables.
        for prop in missing:
            if prop not in PROPERTY_TERMS or not re.search(PROPERTY_TERMS[prop],s,re.I):continue
            unit=UNITS.get(prop)
            if not unit:continue
            qs=[q for q in quantities(s) if q['unit']==unit]
            unsafe=[q for q in qs if not any(display_matches(q,g['value'],g['unit']) for g in given)]
            if unsafe:
                events.append(dict(type='failed_subtask_result_claim',action='replace_local_claim',
                                   original=s,property=prop,reason='No successful receipt for requested field'))
                return '该查询字段本次未取得可验证结果，具体数值不予交付。'
        return s
    text=re.sub(r'[^。！？\n]+[。！？]?',clause,text)
    return text,events


def discard_model_db_tables(text):
    """Model-written DB tables are drafts; authoritative tables use receipts."""
    events=[]
    def table(m):
        block=m[0]
        if not re.search(r'mp-\d+|\[DB\d+\]',block):return block
        events.append(dict(type='model_database_table_replaced',action='use_receipt_table',original=block))
        return ''
    return re.sub(r'(?m)(?:^[ \t]*\|[^\n]*\|[ \t]*(?:\n|$)){2,}',table,text),events


def failed_calculation_delivery(text, message, records, target, explanation):
    """A failed target cannot be completed by the model's own arithmetic."""
    if target['target_complete'] or target['target_state']=='NOT_REQUESTED':return text,[]
    if not any(r['name']=='calculate' for r in records):return text,[]
    event=dict(type='failed_calculation_delivery',action='receipt_owned_failure',
               target_state=target['target_state'],original=text)
    notice='**计算子任务未完成：** 未取得该目标的成功计算回执，不将模型自行推算的值作为结果交付。'
    if not explanation:return notice,[event]
    # Inspect the presentation structure, not just plain-text quantity tokens.
    # LaTeX unit wrappers and Markdown numeric cells must not bypass failure
    # delivery. Keep symbolic methods and independent explanations intact.
    def plain(value):
        value=re.sub(r'\\(?:text|mathrm|mathbf|operatorname)\s*\{([^{}]*)\}',r'\1',value)
        value=value.replace('−','-').replace('\\,',' ').replace('\\!','')
        return re.sub(r'[*`$]','',value)
    scalar=r'[-+]?\s*(?:\d+(?:\.\d*)?|\.\d+)'
    from backend.agent.numeric_validation import quantities
    events=[event]
    def guard(fragment, table=False):
        normalized=plain(fragment)
        # Explicitly labelled supplied values can be restated as inputs, but
        # cannot authorize a newly derived claim merely by value coincidence.
        input_only=bool(re.search(r'题设|用户给定|用户原文|已知输入',normalized)) and not re.search(r'因此|所以|算得|算出|结果|超额|换算后',normalized)
        evaluated=bool(re.search(r'(?:=|≈|\\approx|\\simeq|算得|算出|结果为|等于)\s*\{?\s*'+scalar,normalized))
        evaluated = evaluated or bool(re.search(scalar+r'\s*(?:<|>|≤|≥|\\leq?|\\geq?)\s*'+scalar,normalized))
        dimensional=bool(quantities(normalized)) and bool(re.search(r'=|结果|超额|换算|算得|得到|计算|为',normalized))
        numeric_cell=table and bool(re.search(r'\|\s*'+scalar+r'\s*(?:\||[A-Za-z])',normalized))
        if not input_only and (evaluated or dimensional or numeric_cell):
            events.append(dict(type='unreceipted_calculation_claim',action='withhold_local_result',original=fragment))
            return '该计算目标未完成，具体结果不予交付。'
        return fragment
    # Multiline display equations are one result block; sentence splitting
    # inside decimal points or LaTeX braces would otherwise leave half a value.
    text=re.sub(r'\$\$.*?\$\$|\\\[.*?\\\]',lambda m:guard(m[0]),text,flags=re.S)
    lines=[]
    for line in text.splitlines():
        if line.lstrip().startswith('|'):
            filtered=guard(line,table=True)
            # Remove only rejected result rows, not independent prose.
            if filtered!=line:continue
            lines.append(line)
        else:
            lines.append(re.sub(r'[^。！？\n]+[。！？]?',lambda m:guard(m[0]),line))
    safe='\n'.join(lines)
    # Partial success remains available from actual receipts even when model
    # prose was withheld; never infer units/basis for a unitless math result.
    receipts=[]
    for r in records:
        res=r['result'];out=res.get('result') or {}
        if r['name']=='calculate' and res.get('execution',{}).get('success') and type(out.get('value')) in (int,float):
            receipts.append('| '+str(out.get('expression') or res.get('operation')).replace('|','\\|')+' | '+format(out['value'],'.12g')+' | '+str(out.get('unit') or '未声明')+' | ['+r.get('evidence_id','')+'] |')
    if receipts:
        safe+='\n\n**已完成的子计算（其余目标仍未完成）**\n\n| 实际表达式 | 结果 | 单位 | 回执 |\n|---|---|---|---|\n'+'\n'.join(receipts)
    return safe+'\n\n'+notice,events


def formatted_failure_delivery(message, contract, records, delivery, calculation):
    """A format request is not itself a request for a successful tool receipt.

    Optional searches must not veto an independent YES/NO judgment. Explicit
    database judgments and calculation targets still require real receipts.
    JSON and other result-bearing formats retain the existing failure policy.
    """
    failure = failed_json_delivery(records, delivery, calculation)
    if failure is None:
        return None
    if contract.kind == 'binary':
        positive = re.sub(
            r"(?:不要|无需|不必|禁止|不得|do not|don't|never|without)[^，,。！？!?;；\n]*",
            '', message, flags=re.I)
        source = r'(?:Materials\s+Project|\bMP\b|数据库|database)'
        lookup = r'(?:查询|查阅|检索|核查|查一下|查|query|look\s*up|retrieve|fetch|search|check)'
        explicit_database = bool(re.search(
            lookup + r'[^\n。!?]{0,100}(?:' + source + r'|mp-\d+)|'
            + source + r'[^\n。!?]{0,100}' + lookup + r'|'
            r'(?:根据|依据|according\s+to|as\s+reported\s+by)\s*' + source,
            positive, re.I))
        explicit_entry_property = bool(re.search(r'(?<![A-Za-z0-9_])mp-\d+(?![A-Za-z0-9_])', positive, re.I)
                                       and any(re.search(p, positive, re.I)
                                               for p in PROPERTY_TERMS.values()))
        calculation_requested = calculation['target_state'] != 'NOT_REQUESTED'
        if not (explicit_database or explicit_entry_property or calculation_requested):
            return None
    return failure if contract.kind == 'json' else '工具目标未完成，未取得成功回执，无法按成功结果格式提供数值或结论。'


def failed_json_delivery(records, delivery, calculation):
    """Failure is a machine-readable receipt envelope, never a guessed scalar.

    A requested success-only custom schema cannot always represent failure;
    this envelope makes that limitation explicit while keeping valid JSON.
    """
    failed_mp=any(t['state']!='SUCCESS' for t in delivery['subtasks'])
    failed_calc=calculation['target_state'] not in ('NOT_REQUESTED','SUCCESS') and any(r['name']=='calculate' for r in records)
    if not (failed_mp or failed_calc):return None
    return json.dumps(dict(status='unresolved_or_partial',result=None,
        message='未取得全部目标的成功回执；仅交付真实成功部分，未满足原成功结果schema。',
        materials=[dict(query=r['arguments'].get('material_query'),status=r['result'].get('status'),
                        receipt=r['evidence_id'],records=r['result'].get('candidates',[]) if r['result'].get('status') in ('ok','success') else [])
                   for r in records if r['name']=='search_materials'],
        calculation=dict(state=calculation['target_state'],successful_receipts=[dict(receipt=r['evidence_id'],result=r['result'].get('result'))
            for r in records if r['name']=='calculate' and r['result'].get('execution',{}).get('success')])),ensure_ascii=False)


def normalize_core_aliases(text, sources):
    """Match the frontend's existing numeric citation aliases to visible E IDs.

    This aligns used-source metadata; it does not establish entailment.
    Code spans and unknown numbers are unchanged.
    """
    aliases={str(s['number']):s['evidence_id'] for s in sources if s.get('number') is not None and s.get('evidence_id')}
    events=[]
    def group(m):
        ids=[x.strip() for x in re.split(r'[,，]',m[1])]
        converted=[aliases.get(x,x) for x in ids]
        if converted==ids:return m[0]
        events.append(dict(type='visible_citation_alias_normalized',original=m[0],ids=converted,action='format_only'))
        return ''.join('['+x+']' for x in converted)
    parts=re.split(r'(```.*?```|`[^`]*`)',text,flags=re.S)
    pattern=r'\[((?:E\d+|\d+)(?:\s*[,，]\s*(?:E\d+|\d+))*)\](?!\()'
    return ''.join(p if i%2 else re.sub(pattern,group,p) for i,p in enumerate(parts)),events


def delivery_instruction(scope, records):
    report=delivery_report(scope,records)
    return ('\n交付契约：数据库结果表由后端从真实回执生成，你只解释概念、方法和限制，不重复数据库数值、不自行挂DB引用。'
            '未成功的数据库/计算目标不得用记忆、其他对象或背景片段补成查询结果。用户题设可以明确标为题设。'
            'Core引用仍需直接支持对应概念，不用背景引用代替失败的数据库结果。'
            '\n本轮真实交付状态：'+json.dumps(report,ensure_ascii=False))


def result_table(records, scope=None):
    from backend.agent.facts import UNITS
    sections=[]
    for r in records:
        if r['name']!='search_materials':continue
        res=r['result']; query=r['arguments'].get('material_query',''); eid=r['evidence_id']
        rows=res.get('candidates',[]) if res.get('status') in ('ok','success') else []
        if not rows:
            sections.append(f'**{query}：** 本次查询未取得可验证的数据库结果（{res.get("status","error")}）；不提供该查询的结果值。')
            continue
        props=r['arguments'].get('properties') or ['band_gap','energy_above_hull','space_group','density']
        props=[p for p in props if p in UNITS]
        def cell(v):
            if isinstance(v,float):return format(v,'.8g')
            return str(v if v is not None else '未提供').replace('|','\\|').replace('\n',' ')
        header=['材料编号','化学式']+[p+(f' ({UNITS[p]})' if UNITS[p] else '') for p in props]+['来源']
        lines=['| '+' | '.join(header)+' |','| '+' | '.join(['---']*len(header))+' |']
        if scope and scope.get('sort_by_ehull'):
            rows=sorted(rows,key=lambda row:(row.get('energy_above_hull') is None,row.get('energy_above_hull') or 0,row.get('material_id','')))
        limit=(scope or {}).get('max_rows') or 8
        for row in rows[:limit]:
            lines.append('| '+' | '.join([cell(row.get('material_id')),cell(row.get('formula'))]+[cell(row.get(p)) for p in props]+['['+eid+']'])+' |')
        note=f'**{query} · 实际数据库回执**'
        from backend.agent.session_state import lowest_ehull_requested
        if scope and lowest_ehull_requested(scope.get('original','')):
            energies=[row['energy_above_hull'] for row in rows if isinstance(row.get('energy_above_hull'),(float,int))]
            lowest=[row for row in rows if energies and row.get('energy_above_hull')==min(energies)]
            if len(lowest)==1:note+='；按你的最低Ehull条件，主结果为 **'+lowest[0]['material_id']+'**'
            elif lowest:note+='；最低Ehull有并列条目，保留候选，不唯一选定'
        if len(rows)>limit:note+=f'（返回{len(rows)}条，先列{limit}条，完整结果可在下方候选表内滚动查看）'
        summary = receipt_summary(rows, props, eid)
        sections.append(summary+'\n\n'+note+'\n\n'+'\n'.join(lines))
    return '\n\n'.join(sections)


def receipt_summary(rows, properties, evidence_id):
    """Readable lookup delivery, derived only from the current receipt.

    A display reference is not a unique material selection or a synthesis claim.
    """
    from math import isfinite
    def number(value):
        return type(value) in (int, float) and isfinite(value)
    def fmt(value):
        return format(value, '.6g')
    ranked = sorted(rows, key=lambda r: (
        not number(r.get('energy_above_hull')),
        r['energy_above_hull'] if number(r.get('energy_above_hull')) else 0,
        str(r.get('material_id', ''))))
    ref = ranked[0]
    label = str(ref.get('material_id') or '该条目')
    citation = '[' + evidence_id + ']'
    text = []
    if len(rows) > 1:
        text.append(f'本次查到 **{len(rows)} 个结构条目**。不同结构的性质可能不同。')
        if number(ref.get('energy_above_hull')):
            minimum = ref['energy_above_hull']
            ties = sum(number(r.get('energy_above_hull')) and r['energy_above_hull'] == minimum for r in rows)
            qualifier = '（最低值并列，不代表唯一结构）' if ties > 1 else ''
            text.append(f'以下以本次结果中 **Ehull 最低的 {label}** 为展示参考{qualifier}，其他候选见表格。')
        else:
            text.append('本次没有可用于比较稳定性的完整 Ehull 数据，以下逐条列出已有结果。')
    else:
        text.append(f'本次取得 **{label}** 的数据库记录。')
    if 'band_gap' in properties:
        gap = ref.get('band_gap')
        if number(gap):
            text.append(f'- **带隙：{fmt(gap)} eV**（{label}）。这是数据库所用计算方法下的结果，不等同于实验测量值。{citation}')
        else:
            text.append(f'- **带隙：** {label} 本次未返回该字段，不补充猜测值。')
    if 'energy_above_hull' in properties:
        hull = ref.get('energy_above_hull')
        if number(hull):
            if hull == 0:
                meaning = '在数据库采用的计算方法和竞争相集合下，该结构位于能量凸包上；这不等于任何温度、压力下都稳定，也不能单凭它保证可合成或电化学稳定。'
            elif hull > 0:
                meaning = '在数据库采用的计算方法和竞争相集合下，该结构高于凸包，存在能量更低的竞争相组合；这本身不排除亚稳态材料的实验存在。'
            else:
                meaning = '该返回值需要结合数据库能量处理方式核对，不据此额外推断稳定性。'
            text.append(f'- **稳定性：Ehull = {fmt(hull)} eV/atom**（{label}）。{meaning}{citation}')
        else:
            text.append(f'- **稳定性：** {label} 本次未返回 Ehull，不能据此确定其凸包稳定性。')
    if 'formation_energy_per_atom' in properties and number(ref.get('formation_energy_per_atom')):
        text.append(f'- **形成能：{fmt(ref["formation_energy_per_atom"])} eV/atom**（{label}）。它比较元素参考态，不能单独替代竞争相凸包判断。{citation}')
    return '\n\n'.join(text)
