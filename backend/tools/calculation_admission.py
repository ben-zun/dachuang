"""Backend-owned calculation admission. Only user declarations resolve ambiguity.

This is a bounded unit/relation interface, not a general physical-model verifier.
READY is intermediate; execution/transport failures retain their own statuses.
"""
from copy import deepcopy
import re

STATES={'READY','NEEDS_CLARIFICATION','UNSUPPORTED_RELATION','INVALID_INPUT','SUCCESS','NOT_REQUESTED'}
SCALAR=r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?'
ALIASES={'摄氏度':'°C','℃':'°C','开尔文':'K','电子伏特':'eV','焦耳':'J','每摩尔原子':'mol_atoms','每摩尔化学式':'mol_formula'}
UNITS=['kJ/(mol_formulaK)','J/(mol_formulaK)','kJ/(mol_atomsK)','J/(mol_atomsK)','kJ/(molK)','J/(molK)','meV/(atomK)','eV/(atomK)','J/mol_formula','kJ/mol_formula','J/mol_atoms','kJ/mol_atoms','meV/atom','eV/atom','eV/fu','kJ/mol','J/mol','g/cm3','kg/m3','rad/s','degC','meV','eV','°C','kg','nm','um','mm','cm','GPa','MPa','Pa','Å','K','g','J','m']
from backend.tools.unit_registry import ADDITIONAL_UNITS
UNITS=list(dict.fromkeys(UNITS+list(ADDITIONAL_UNITS)))
UPAT='(?:'+'|'.join(re.escape(u)for u in sorted(UNITS,key=len,reverse=True))+')'
UNIT_END=r'(?![A-Za-z0-9_/^⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺])'
UNIT=re.compile(r'(?<![A-Za-z_/])'+UPAT+UNIT_END)
QUANTITY=re.compile(r'(?<![A-Za-z0-9_.^+\-])('+SCALAR+')('+UPAT+')'+UNIT_END)

def compact(text):
    text=text.replace('−','-').replace('·','').replace('⋅','').replace('\\,','')
    # Equivalent spellings of the same explicit energy normalization basis.
    text=re.sub(r'eV\s*/\s*(?:每(?:个)?化学式(?:单位)?|化学式(?:单位)?|formula(?:[ _-]*units?)?|f\.?\s*u\.?)', 'eV/fu', text)

    text=re.sub(r'(k?J)/mol\s*(?:fu|formula(?:[\s_]*units?)?)\b',r'\1/mol_formula',text,flags=re.I)
    text=re.sub(r'(k?J)/mol\s*atoms?\b',r'\1/mol_atoms',text,flags=re.I)
    text=text.replace('μm','um').replace('µm','um').replace('³','3')
    text=re.sub(r'([-+]?(?:\d+(?:\.\d*)?|\.\d+))\s*(?:×|\\times|\*)\s*10\s*\^\s*\{?([-+]?\d+)\}?',r'\1e\2',text)
    for a,b in {'摄氏度':'°C','℃':'°C','开尔文':'K','电子伏特':'eV','焦耳':'J', '小时':'h','分钟':'min',
                '毫秒':'ms','微秒':'us','纳秒':'ns','皮秒':'ps','飞秒':'fs','秒':'s'}.items():text=text.replace(a,b)
    text=re.sub(r'摄氏(?:温差|温度)', '°C', text)
    names={'米':'m','毫米':'mm','微米':'um','纳米':'nm','厘米':'cm'}
    text=re.sub(r'([0-9.]|多少|用|成|到|为)\s*(毫米|微米|纳米|厘米|米)',lambda m:m[1]+names[m[2]],text)
    return re.sub(r'\s+','',text)

def prohibited(text):
    from backend.tools.execution_contract import permission
    return permission(text)['state']!='ALLOW'


def basis(text):
    # Do not infer target mol from the source word atom in eV/(atom K).
    if re.search(r'(?:尚未|未确定|不清楚|不明确|未确认).{0,24}mol|mol.{0,24}(?:还是|未确定|不明确)|原子(?:还是|和|及|或|/)化学式',text,re.I):return 'conflict'
    atom=bool(re.search(r'每摩尔原子|mol_atoms|按原子|mol(?:指|按|是|明确指)(?:每摩尔)?原子',text,re.I))
    fu=bool(re.search(r'每摩尔(?:化学式|(?:[A-Z][a-z]?\d*)+化学式)|mol_formula|formula_units|按化学式|mol(?:指|按|是|明确指)(?:每摩尔)?(?:化学式|(?:[A-Z][a-z]?\d*)+化学式)',text,re.I))
    return 'atoms' if atom and not fu else 'formula_units' if fu and not atom else 'conflict' if atom and fu else None

def formulas(text):
    from backend.tools.calculation import composition
    out=[]
    for token in re.findall(r'(?<![A-Za-z])(?:[A-Z][a-z]?\d*)+(?![A-Za-z])',text):
        if token in ('K','J','Pa','E','T','A','JSON','Calculate'):continue
        try:
            c=composition(token)
            if len(c)>1 or re.search(r'\d',token):out.append(token)
        except (ValueError,TypeError):pass
    return list(dict.fromkeys(out))

def terminal(status,message,**data):return dict(status=status,message=message,**data)


def temperature_kind(text):
    """Negated labels are not declarations of the requested temperature object."""
    positive=re.sub(r'(?:不是|并非|非|not\s+(?:an?\s+)?)\s*(?:绝对温度|absolute temperature|温度差|温差|temperature difference)', '', text, flags=re.I)
    difference=bool(re.search(r'温差|温度差|ΔT|降温|升温|temperature difference',positive,re.I))
    absolute=bool(re.search(r'绝对温度|absolute temperature',positive,re.I))
    return 'conflict' if difference and absolute else 'difference' if difference else 'absolute'

def assess(text,pending=None):
    """Extract only explicit user quantities/units; pending contains user data only."""
    if prohibited(text):return terminal('NOT_REQUESTED','本轮不执行计算。',request=None,pending=None)
    s=compact(text)
    q=list(QUANTITY.finditer(s));units=list(UNIT.finditer(s))
    # A requested derived quantity is not the last observation in its data
    # list, even when its output unit differs from the observations' unit.
    # A user-supplied statistic ("RMSE=3 meV, 换成eV") remains convertible.
    derived_name=r'(?:(?<![A-Za-z])(?:RMSE|MAE|ZT)(?![A-Za-z])|均方根(?:误差)?|均值|平均值?|标准差|方差|中位数|相对密度|损失率|百分比|百分率)'
    if re.search(derived_name,text,re.I) and not re.search(derived_name+r'\s*(?:=|为|是|约为|约)\s*'+SCALAR+r'\s*'+UPAT,text,re.I):
        return terminal('READY','用户要求计算派生量；保留完整运算目标，不能把单个观测值当作最终结果换算。',request=None,pending=None,user_sources=[text])
    declared_now=basis(s)
    continuing=bool(re.search(r'继续|刚才|上(?:一)?轮|这里mol|按.*定义|材料是',text,re.I)) or bool(pending and declared_now in ('atoms','formula_units'))
    if pending and continuing and not q:
        data=deepcopy(pending['data']);sources=deepcopy(pending['user_sources'])+[text]
    else:
        data={};sources=[text]
        if pending and not continuing:pending=None
    conversion_words=bool(re.search(r'换算|转换|换成|换到|转成|转为|转|折合|相当于|等于多少|用.{0,20}表示|写成|表示为|convert|express',text,re.I))
    if len(q)>1:
        return terminal('READY','包含多个物理输入，不能擅自简化为单一单位换算。',request=None,pending=None,user_sources=sources)
    if q:
        first=q[0];data.update(value=float(first[1]),from_unit=first[2])
        targets=[m[0]for m in units if not any(a.start()<=m.start()<a.end()for a in q)]
        if targets:data['to_unit']=targets[-1]
        elif len(q)>1:data['to_unit']=q[-1][2]
    elif not data and continuing and re.search(r'换算|计算|继续算',re.sub(r'(?:不必|无需|不用|不要|(?<!分)别)[^，。；;]*','',text)):
        return terminal('NEEDS_CLARIFICATION','请重新给出需要计算的数值和单位；当前没有待继续的计算。',pending=None)
    declared=basis(text) or basis(s)
    if declared:data['molar_basis']=declared
    fs=formulas(text)
    if fs:
        if len(fs)==1:data['formula']=fs[0]
        else:data['formula_conflict']=True
    kind=temperature_kind(text)
    if kind=='conflict':return terminal('NEEDS_CLARIFICATION','请明确换算的是温差还是绝对温度。',request=None,pending=None)
    if re.search(r'温差|温度差|ΔT|降温|升温|绝对温度|temperature',text,re.I):data['temperature_kind']=kind
    if re.search(r'E=k_?B\*?T|E=k_?BT',s,re.I):data['relation']='E=k_B*T'
    if data.get('relation')=='E=k_B*T' and re.search(r'求(?:对应)?\s*E|求(?:对应)?能量|(?:计算|求出)能量',text,re.I):
        # The requested quantity is energy. A K in eV/K is not an output unit.
        ts=[m for m in q if m[2]=='K']
        if len(ts)==1:
            data.update(value=float(ts[0][1]),from_unit='K',to_unit='eV',relation='E=k_B*T')
    if re.search(r'按|采用|关系|=',text) and re.search(r'E=a\*?k_?B\*?T',s,re.I):data['unknown_coefficient']=True
    relation_request=bool(re.search(r'反应速率|速率常数|E=ħ|E=hbar|E=a\*?k_?B',s,re.I))
    conversion=conversion_words or bool(re.search(r'对应|是多少',text)) or continuing and bool(data)
    if not conversion and not relation_request:return terminal('READY','参数仍须在实际工具请求中校验。',request=None,pending=None)
    if not data.get('from_unit') or not data.get('to_unit'):
        if relation_request:
            if re.search(r'反应速率|速率常数',text):
                return terminal('NEEDS_CLARIFICATION','计算速率还需要明确速率关系、温度及前因子；请补充这些输入。',pending=None)
            return terminal('UNSUPPORTED_RELATION','当前计算器未实现这项关系的类型化计算；可说明方法，但不会自行替换成另一物理关系。',pending=None)
        return terminal('READY','未识别为单一单位换算，实际请求仍需校验。',request=None,pending=None)
    src,dst=data['from_unit'],data['to_unit']
    # Repeating an observation's unit ("RMSE ... 用meV表示") does not
    # declare a unit conversion. Leave the full arithmetic task to Calculate.
    if src==dst:
        return terminal('READY','源单位与输出单位相同；仍需执行用户要求的运算，不建立恒等换算。',request=None,pending=None,user_sources=sources)
    def wait(message):return terminal('NEEDS_CLARIFICATION',message,pending=dict(data=data,user_sources=sources),user_sources=sources)
    generic=any(u in ('J/mol','kJ/mol','J/(molK)','kJ/(molK)')for u in (src,dst))
    if generic and data.get('molar_basis') not in ('atoms','formula_units'):
        return wait('这里的 mol 指每摩尔原子，还是每摩尔化学式单位？请明确后再计算。')
    def ub(u):
        if '/atom' in u or '/(atom' in u or 'mol_atoms' in u:return 'atoms'
        if '/fu' in u or 'mol_formula' in u:return 'formula_units'
        if 'mol' in u:return data.get('molar_basis')
        return None
    if ub(src) and ub(dst) and ub(src)!=ub(dst) and (not data.get('formula') or data.get('formula_conflict')):
        return wait('原子基准与化学式基准之间换算需要明确化学式。请提供材料化学式。')
    if src=='K' and dst in ('eV','meV','J') and data.get('relation')!='E=k_B*T':
        return wait('温度与能量不是同一维度，请明确E=k_B*T等物理关系。')
    if src in ('eV','meV','J') and dst=='K':
        if data.get('unknown_coefficient'):return wait('关系中的系数 a 尚未确定，请给出 a；不会默认它等于1。')
        if data.get('relation')!='E=k_B*T':return wait('能量与温度不是同一维度。是否按 E=k_B*T 定义等效温度？或请指定其他关系。')
    elif relation_request:
        return terminal('UNSUPPORTED_RELATION','当前计算器未实现这项明确关系的类型化计算，不会替换成其他关系。',pending=None)
    if src in ('K','°C','degC') and dst in ('K','°C','degC'):
        data.setdefault('temperature_kind','absolute')
        if data['temperature_kind']=='absolute' and data['value']+(273.15 if src in ('°C','degC')else 0)<0:
            return terminal('INVALID_INPUT','该绝对温度低于0 K，不能按普通摄氏/开尔文温标执行换算。',pending=None)
    inputs={k:v for k,v in data.items()if k in ('value','from_unit','to_unit','formula','molar_basis','temperature_kind','relation')}
    request={'operation':'convert_units','inputs':inputs}
    # Both declarations and the exact original user messages are retained.
    canonical=f"{inputs['value']} {src} 换算成 {dst}"
    if data.get('formula'):canonical+=' 化学式 '+data['formula']
    if data.get('molar_basis'):canonical+=' mol按'+('原子'if data['molar_basis']=='atoms'else '化学式')
    if data.get('temperature_kind'):canonical+=' 温差'if data['temperature_kind']=='difference'else ' 绝对温度'
    if data.get('relation'):canonical+=' 按 E=k_B*T'
    return terminal('READY','输入已明确；尚未执行。',request=request,pending=None,user_sources=sources,canonical_context=canonical)


def authorize_call(args,text):
    """All operations, including math, must respect an unresolved request."""
    from backend.tools.execution_contract import prepare
    gate=prepare(text)
    if gate['status']in ('NEEDS_CLARIFICATION','UNSUPPORTED_RELATION','INVALID_INPUT','NOT_REQUESTED'):return gate
    plan=gate.get('request') or gate.get('canonical_prefix')
    if plan and args!=plan and not (args.get('operation')=='math' and args.get('inputs',{}).get('variables') and re.search(r'再乘|再除|再加|再减',text)):
        return terminal('INVALID_INPUT','计算参数与用户声明的单位/基准/关系不一致；通用math也不能绕过类型化换算。')
    return gate


def receipt(result,admission):
    if result.get('status')!='ok' or not result.get('execution',{}).get('success'):return result
    data=result.get('result',{});inputs=result.get('inputs',{})
    result['calculation_state']='SUCCESS'
    result['calculation_contract']=dict(input_value=inputs.get('value'),input_unit=inputs.get('from_unit'),input_basis=(admission.get('request')or{}).get('inputs',{}).get('molar_basis'),target_unit=inputs.get('to_unit'),target_basis=data.get('basis'),formula_or_relation=inputs.get('relation')or inputs.get('formula'),expression=data.get('expression')or inputs.get('expression'),result=data.get('value'),result_unit=data.get('unit'),result_basis=data.get('basis'),source='user'if admission.get('user_sources')else 'validated_tool_or_derived',user_sources=admission.get('user_sources',[]),scope='执行与已声明关系下的算术/换算；不证明物理模型适用性')
    return result
