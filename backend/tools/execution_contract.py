"""Backend-owned permission, canonical requests and target completion.

No reader/retriever policy. A successful helper operation is not target success.
"""
import re
import math
from collections import Counter
from backend.tools.calculation_admission import assess, compact, formulas, QUANTITY, UNIT

# Surface forms of the same explicit output basis; not scientific inference.
PER_FORMULA = r'每(?:个)?(?:输入)?(?:(?:[A-Z][a-z]?\d*)+)?化学式(?:单位)?|per\s+(?:input\s+)?formula(?:\s+unit)?|eV/(?:formula(?:_unit)?|fu)'


def permission(text):
    if re.search(r'(?:暂时|先)?(?:不要|(?<!分)别|请勿|禁止|不得|不必|无需)\s*(?:再|重新|进行|执行|调用|做)*\s*(?:第?[一二三四五六七八九十0-9]+次)?\s*(?:Calculate|计算|换算|数值计算)',text,re.I):
        state='WAIT_CONFIRMATION' if re.search(r'暂时|先|等.*确认',text) else 'FORBIDDEN'
        return {'state':state,'message':'本轮不执行Calculate，也不提前推算结果；独立的解释任务仍可回答。'}
    wait = re.search(r'等(?:待)?我(?:明确)?确认|等待(?:确认|批准)|确认(?:后|之前|前)再?|先(?:别|不|不要)(?:算|计算|换算|执行)|暂(?:时)?(?:先)?(?:不|(?<!分)别|不要)(?:算|计算|换算)|hold off|wait for (?:my )?confirmation', text, re.I)
    forbid = re.search(r'取消|停止(?:计算|换算)|(?:不要|(?<!分)别|请勿|禁止|不得|不必|不需要|无需|无须|不)(?:再|进行|做|调用|使用|执行)?\s*(?:Calculate|计算|换算|算)|(?:不要|(?<!分)别|请勿|暂时不要)(?:给出?|输出|提供)?\s*(?:数值|数字|结果)|\b(?:do not|don.t|never|without)\s+(?:use\s+|call\s+|perform\s+)?(?:calculate|calculation|convert|compute)|no numerical (?:results|calculation)', text, re.I)
    if wait:
        return {'state':'WAIT_CONFIRMATION','message':'本轮等待你明确确认，不执行任何 Calculate 操作，也不提前给出数值结果。'}
    if forbid:
        return {'state':'FORBIDDEN','message':'本轮按你的要求不执行 Calculate，也不做数值推算。'}
    return {'state':'ALLOW','message':'仍须满足输入、基准及关系准入。'}


def prepare(text, pending=None, user_records=(), trusted_quantities=()):
    permit=permission(text)
    restored=False
    if pending and pending.get('kind')=='execution_hold' and re.search(r'继续|确认|同意|可以算|开始算',text):
        if permit['state']=='ALLOW' and re.search(r'确认|同意|可以算|开始算',text):
            # Permission is decided from the current message. Drop whole old
            # permission clauses, including "别给换算后的数值", not fragments.
            old=re.split(r'[，,。；;]',pending['user_text'])
            kept=[]
            for c in old:
                if permission(c)['state']=='ALLOW' and not re.search(r'(?:不要|别).*(?:数值|结果)',c):
                    kept.append(c)
                elif QUANTITY.search(compact(c)) or '=' in c:
                    c=re.sub(r'先(?:别|不|不要)(?:计算|换算|执行|算)|(?:现在|本轮)?(?:不要|请勿|别)(?:代入)?(?:计算|换算|执行Calculate|算)', '', c,flags=re.I)
                    c=re.sub(r'等(?:待)?我(?:明确)?确认|等待(?:确认|批准)', '',c)
                    if permission(c)['state']=='ALLOW':kept.append(c)
            old=kept
            text='；'.join(old)+'；'+text
            restored=True
            pending=None
        elif permit['state']=='ALLOW':
            permit={'state':'WAIT_CONFIRMATION','message':'上轮仍在等待明确确认，本轮不执行计算。'}
    if permit['state']!='ALLOW':
        return {'status':'NOT_REQUESTED','request':None,'pending':({'kind':'execution_hold','user_text':pending['user_text'] if pending and pending.get('kind')=='execution_hold' else text} if permit['state']=='WAIT_CONFIRMATION' else None),
                'permission':permit,'message':permit['message']}
    if pending and pending.get('kind')=='formula_needed' and re.search(r'继续|材料|化学式',text):
        text=pending['user_text']+'；'+text
        pending=None
    # A cross-turn operand must be one unique real receipt, never assistant
    # prose or a guessed material. Its reference survives confirmation.
    reference_request=None
    if re.search(r'刚才|上轮|上一轮|同一条目',text) and re.search(r'密度',text):
        sources=[q for q in trusted_quantities if q.get('property')=='density' and q.get('source_type')=='tool_fact']
        unique={q['ref']:q for q in sources}
        destinations=[m[0] for m in UNIT.finditer(compact(text))]
        if len(unique)==1 and destinations:
            q=next(iter(unique.values()))
            reference_request={'operation':'convert_units','inputs':{'value':{'ref':q['ref']},'from_unit':q['unit'],'to_unit':destinations[-1]}}
    gate=assess(text,pending)
    # A unique real formation-energy receipt can satisfy per-atom -> per-formula.
    if re.search(PER_FORMULA,compact(text),re.I) and re.search(r'同一条目|刚才|上轮|上一轮',text):
        sources=[q for q in trusted_quantities if q.get('property')=='formation_energy_per_atom' and q.get('source_type')=='tool_fact']
        unique={q['ref']:q for q in sources}
        fs=formulas(text)
        if len(unique)==1 and len(fs)==1:
            q=next(iter(unique.values()))
            if q.get('formula')==fs[0]:
                reference_request={'operation':'energy_per_formula','inputs':{'value':{'ref':q['ref']},'formula':fs[0]}}
    if reference_request:
        gate=dict(status='READY',message='使用会话中的真实数据库字段换算。',request=reference_request,pending=None,
                  canonical_context=text+' 化学式 '+str((reference_request.get('inputs') or {}).get('formula','')),user_sources=[text])
    if restored:
        gate['user_sources']=[text]
        gate['execution_context']=text
    gate['permission']=permit
    # The existing supported eV/atom -> eV/fu operation, now owned by backend.
    if re.search(PER_FORMULA,compact(text),re.I) and not re.search(r'再乘|再除|再加|再减',text):
        qs=[q for q in QUANTITY.finditer(compact(text)) if q[2]=='eV/atom']
        fs=formulas(text)
        original=[text]
        v=float(qs[0][1]) if len(qs)==1 else None
        if not qs and re.search(r'仍使用|沿用|刚才给定|上一轮给定',text):
            old=[q for q in user_records if q.get('unit')=='eV/atom' and q.get('source_type')=='user_given']
            if old:
                values={q['value'] for q in old}
                if len(values)==1:
                    v=old[-1]['value'];original=[old[-1].get('text',''),text]
        if v is not None:
            if len(fs)!=1:
                gate.update(status='NEEDS_CLARIFICATION',request=None,pending={'kind':'formula_needed','user_text':text},
                            message='每原子到每化学式的换算还需要明确唯一的化学式。')
            else:
                req={'operation':'energy_per_formula','inputs':{'value':v,'formula':fs[0]}}
                gate.update(status='READY',request=req,pending=None,user_sources=original,
                    canonical_context=f'{v} eV/atom 化学式 {fs[0]}，计算每化学式能量；仅归一化，不改变能量定义。')
    if gate.get('request') and re.search(r'再乘|再除|再加|再减',text):
        gate['canonical_prefix']=gate.pop('request')
        gate['request']=None
    return gate


def text_task(text):
    """Keep independent explanation clauses; never ask the reader to fill a blocked calculation."""
    clauses=re.split(r'[，,；;。\n]|(?:然后|再把|并把|并将)',text)
    selected=[]
    for c in clauses:
        c=re.sub(r'(?:但|而且)?(?:不要|请勿|不必|无需|只需不|先别|先不)(?:进行)?(?:计算|换算|算).*','',c).strip()
        if (re.search(r'解释|说明|为什么|为何|比较|区别|只讲|只谈|理解|介绍|讲解|讲讲|阐述|\bexplain\b|\bdescribe\b',c)
                and not re.search(r'当前工具|工具若|若不支持|工具.*支持|输入有效',c)):
            selected.append(c)
    if selected:
        # Definitions supplied before "解释每个量" are needed to identify the
        # symbols. They are context, not permission to execute the calculation.
        context=[c for c in clauses if permission(c)['state']=='ALLOW' and ('=' in c or
                 (re.search(r'密度|厚度|温度|能量|长度|体积',c) and UNIT.search(compact(c))))]
        return '；'.join(dict.fromkeys(context+selected))
    return ''



def goal_signature(request):
    """A bounded physical goal, shared by equivalent supported operations."""
    op=request.get('operation');inputs=request.get('inputs') or {}
    if op=='energy_per_formula':
        return dict(quantity='energy',value=inputs.get('value'),from_unit='eV/atom',
                    to_unit='eV/fu',formula=inputs.get('formula'),molar_basis='formula_units',
                    relation=None,temperature_kind=None)
    if op=='convert_units':
        def unit(u):
            return {'eV/formula':'eV/fu','eV/formula_unit':'eV/fu'}.get(u,u)
        src,dst=unit(inputs.get('from_unit')),unit(inputs.get('to_unit'))
        energy=src=='eV/atom' and dst=='eV/fu'
        return dict(quantity='energy' if energy else 'unit_conversion',value=inputs.get('value'),
                    from_unit=src,to_unit=dst,formula=inputs.get('formula'),
                    molar_basis='formula_units' if energy else inputs.get('molar_basis'),
                    relation=inputs.get('relation'),temperature_kind=inputs.get('temperature_kind'))
    return None


def satisfies_goal(plan, record):
    """Only actual successful receipts can satisfy a target; helpers cannot."""
    result=record.get('result') or {}
    if not result.get('execution',{}).get('success'):return False
    wanted=goal_signature(plan)
    if wanted is None:return record.get('arguments')==plan
    actual=goal_signature({'operation':result.get('operation'), 'inputs':result.get('inputs')})
    if actual is None:return False
    for key in wanted:
        a,b=wanted[key],actual[key]
        if key=='value':
            if isinstance(a,dict) and set(a)=={'ref'}:
                if a['ref'] not in result.get('provenance',{}).get('parents',[]):return False
                continue
            if type(a) not in (int,float) or type(b) not in (int,float) or not math.isclose(a,b,rel_tol=1e-12,abs_tol=1e-12):return False
        elif a!=b:return False
    output=result.get('result') or {}
    # Result labels must agree as well as the resolved input contract.
    if output.get('unit')!=wanted['to_unit']:return False
    if wanted['formula'] and output.get('formula')!=wanted['formula']:return False
    if wanted['quantity']=='energy' and output.get('basis') not in (None,'formula_units','explicit_unit'):return False
    return True


def target_report(admission, records, required):
    ops=[{'operation':r['arguments'].get('operation'),'call_id':r['id'],
          'state':'SUCCESS' if r['result'].get('execution',{}).get('success') else 'FAILED',
          'calculation_state':r['result'].get('calculation_state'),
          'result_id':r['result'].get('result_id')}
         for r in records if r['name']=='calculate']
    permit=admission['permission']['state']
    plan=admission.get('request')
    status=admission['status']
    successes=[r for r in records if r['name']=='calculate' and r['result'].get('execution',{}).get('success')]
    complete=False
    if permit!='ALLOW': state='FORBIDDEN' if permit=='FORBIDDEN' else 'WAIT_CONFIRMATION'
    elif status=='NEEDS_CLARIFICATION': state=status
    elif status=='UNSUPPORTED_RELATION': state='UNSUPPORTED'
    elif status=='INVALID_INPUT': state=status
    elif plan:
        complete=any(satisfies_goal(plan,r) for r in successes)
        state='SUCCESS' if complete else 'PARTIAL' if successes else 'INVALID_INPUT'
    elif required:
        # Required operations are named in user input; composition cannot satisfy energy/math targets.
        requested=admission.get('target_operations',[])
        counts=Counter(r['arguments'].get('operation') for r in successes)
        complete=bool(requested) and all(counts[op]>=n for op,n in Counter(requested).items())
        state='SUCCESS' if complete else 'PARTIAL' if successes else 'INVALID_INPUT'
    else:state='NOT_REQUESTED'
    return {'permission':permit,'target_state':state,'target_complete':complete,
            'canonical_request':plan,'goal':goal_signature(plan) if plan else None,'operations':ops}
