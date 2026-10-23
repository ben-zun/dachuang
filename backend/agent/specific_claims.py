"""Narrow source-name checks; not a scientific entailment verifier."""
import json
import re

POLICY='''具体事实约束：材料数值、身份、软件接口名、论文实验条件只按真实回执/提供证据表述，不主动补未核实的具体事实。
引用仅支持原文实际说出的结论，不能把建议或局部条件扩大为保证。没有资料时继续解释一般原理，但不要自拟API/Class名、实验温度、寿命或材料数值。
软件资料未给出确切接口名时，用功能描述；不提供看似可运行但接口名未核实的代码。用户提到的名称不等于其存在已被验证。
工具失败必须明确其状态；题设或一般单位关系仍可推导，但推导不是工具执行成功。标准公式、常数、题设和有来源计算可以正常使用。'''
POLICY+='\n具体方法名、API/Class名、软件参数名只能来自本次实际证据、工具schema或程序列出的允许名称；用户提及和模型记忆不算核实。找不到确切名称就描述功能，不靠命名猜能力。\nstructure_label及structure_label_source是本站展示/溯源字段，来自本地映射或空间群/晶系，不得称MP官方原始字段。完成请求后不要额外补实测晶格常数、实验带隙或经验数值例子。'

SOFTWARE=re.compile(r'(?<![A-Za-z0-9_])(?:ASE|Phonopy|LAMMPS|pymatgen|mp-api|mp_api|MACE|NequIP|OpenKIM)(?![A-Za-z0-9_])',re.I)
UNCONFIRMED=re.compile(r'未核实|未确认|无法确认|不能确认|不应(?:假定|使用|编造)|不要(?:猜|编造)|不存在|未找到|没有(?:可靠)?(?:资料|证据)|尚未')
BUILTINS={'print','float','int','len','range','list','dict','str','True','False','None','sum','abs','min','max','zip','enumerate'}

def evidence_text(sources):
    # Do not whitelist a hallucination merely because a user query or an old
    # assistant answer mentioned its name. Only actual supplied evidence counts.
    return '\n'.join(str(s.get(k) or '') for s in sources for k in ('title','text'))

def check_specific_names(answer, message, sources, contract, allowed_names=()):
    """Audit specific identifiers only; never rewrite scientific prose or JSON."""
    if not SOFTWARE.search(message+' '+answer) and not re.search(r'\bNEB\b',message+' '+answer):return answer,[]
    supported=evidence_text(sources)+'\n'+'\n'.join(allowed_names);events=[]
    def inspect(text):
        spans=[]
        # Names explicitly rendered as code, dotted Python paths, and named
        # filter/optimizer/calculator classes. Ordinary prose/math is untouched.
        patterns=[r'\b([A-Z][a-z]+(?: [A-Z][a-z]+){0,3} NEB)\b',r'(?<!`)`([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*(?:\(\))?)`(?!`)',
                  r'(?<![A-Za-z0-9_])((?:ase|pymatgen|phonopy|mace|nequip|mp_api)(?:\.[A-Za-z_]\w*)+)(?![A-Za-z0-9_])',
                  r'\b([A-Z][A-Za-z0-9_]*(?:Filter|Optimizer|Calculator|Reader|Writer|Client|Adapter))\b']
        for pattern in patterns:
            for m in re.finditer(pattern,text):
                token=m[1];name=token.removesuffix('()')
                if '.' not in name and re.fullmatch(r'(?:[A-Z][a-z]?\d*)+',name):
                    try:
                        from pymatgen.core import Composition
                        Composition(name);continue
                    except (ValueError,TypeError):pass
                if name in BUILTINS or name in ('ASE','Phonopy','LAMMPS','MACE','NequIP','OpenKIM'):continue
                # Only code-like names: uppercase parameter acronyms and
                # chemical formulae are not class/function claims here.
                if ' ' not in name and '.' not in name and '(' not in token and (name.isupper() or not re.search(r'[a-z][A-Z]|_',name)):continue
                prefix=re.split(r'[。！？\n]',text[:m.start()])[-1][-80:]
                if UNCONFIRMED.search(prefix):continue
                if re.search(r'(?<![A-Za-z0-9_])'+re.escape(name)+r'(?![A-Za-z0-9_])',supported):continue
                spans.append((m.start(1),m.end(1),token))
        used=set()
        for start,end,token in sorted(spans,reverse=True):
            if any(start<b and end>a for a,b in used):continue
            used.add((start,end))
            events.append(dict(type='unsupported_specific_identifier',original=token,action='audit_only'))
        return text
    if contract.kind=='json':
        try:
            obj=json.loads(answer)
            def visit(v):
                if isinstance(v,str):return inspect(v)
                if isinstance(v,list):return [visit(x) for x in v]
                if isinstance(v,dict):return {k:visit(x) for k,x in v.items()}
                return v
            answer=json.dumps(visit(obj),ensure_ascii=False)
        except (ValueError,TypeError):pass
    else:answer=inspect(answer)
    return answer,events

def check_definitions(answer, contract, unspecified_energy=False):
    """Audit free scientific prose without changing negation, cause or meaning."""
    events=[]
    if 'structure_label' in answer and re.search(r'Materials\s*Project|\bMP\b',answer):
        events.append(dict(type='field_provenance_review',action='audit_only'))
    if unspecified_energy and re.search(r'总能|形成能|束缚态',answer):
        events.append(dict(type='undefined_energy_definition_review',action='audit_only'))
    return answer,events


def capabilities(tools):
    """Derived from the schemas actually exposed on this request, never a copy."""
    out={}
    for tool in tools:
        f=tool['function'];props=f['parameters'].get('properties',{})
        item={'arguments':list(props)}
        if 'filters' in props:item['filters']=list(props['filters'].get('properties',{}))
        if 'properties' in props:item['properties']=props['properties'].get('items',{}).get('enum',[])
        if 'operation' in props:item['operations']=props['operation'].get('enum',[])
        out[f['name']]=item
    return out


def scope_policy(records):
    mp=[r for r in records if r['name']=='search_materials' and r['result'].get('status') in ('ok','success')]
    scope={'successful_MP_receipts_current_turn':len(mp),
           'material_ids':[x['material_id'] for r in mp for x in r['result'].get('candidates',[]) if 'material_id' in x]}
    return ('本轮数据库回执范围：'+json.dumps(scope,ensure_ascii=False)+
        '。只有本轮真实MP回执覆盖的对象和属性，才可说“Materials Project返回/数据库已确认”。'
        '无回执时不新增数据库具体结论，不把用户假设说成数据库事实；历史真实记录只能明确称此前回执。'
        '一般原理、题设推导仍正常解释，不因没查库拒答整题。')


def audit_attribution(answer, records):
    if any(r['name']=='search_materials' and r['result'].get('status') in ('ok','success') for r in records):return []
    if re.search(r'Materials\s*Project|\bMP\b|数据库',answer,re.I) and re.search(r'(?:返回|查到|已确认|证实|根据.*数据|数据库.*(?:为|是)\s*[0-9])',answer):
        return [dict(type='database_attribution_without_current_receipt_review',action='audit_only')]
    return []
