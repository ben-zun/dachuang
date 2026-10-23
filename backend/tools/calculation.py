"""Bounded deterministic material calculations. No generated Python execution."""
import ast
import json
import math
import operator
import re
from scipy.spatial import QhullError
from scipy.constants import atomic_mass
from datetime import datetime, timezone
from time import perf_counter
import numpy as np
from pymatgen.core import Composition, Element, Lattice
from pymatgen.analysis.phase_diagram import PhaseDiagram, PDEntry
from backend.paths import ROOT

EV_J = 1.602176634e-19
NA = 6.02214076e23
OPS = {'energy_per_formula', 'math', 'convert_units', 'composition', 'crystal', 'phase_stability', 'reaction_energy'}
# Category and scale to the category's base unit. Per-atom energy is distinct.
UNITS = {'amu':('mass',atomic_mass),'Å³':('volume',1e-30),'Å3':('volume',1e-30),'Å^3':('volume',1e-30),'cm³':('volume',1e-6),'cm3':('volume',1e-6),'cm^3':('volume',1e-6),'amu/Å³':('density',atomic_mass*1e30),'amu/Å3':('density',atomic_mass*1e30),'amu/Å^3':('density',atomic_mass*1e30),'g/cm³':('density',1000),'g/cm^3':('density',1000),'g':('mass',.001),'kg':('mass',1),'eV':('energy',EV_J),'meV':('energy',EV_J/1000),'J':('energy',1),
 'eV/atom':('per_atom',1),'meV/atom':('per_atom',.001),
 'kJ/mol':('per_atom',1000/(NA*EV_J)),
 'Å':('length',1e-10),'angstrom':('length',1e-10),'nm':('length',1e-9),'m':('length',1),
 'g/cm3':('density',1000),'kg/m3':('density',1),
 'Pa':('pressure',1),'MPa':('pressure',1e6),'GPa':('pressure',1e9),
 'm³':('volume',1),'m3':('volume',1),'m^3':('volume',1),
 'g/mol':('molar_mass',.001),'kg/mol':('molar_mass',1)}
# Typographic aliases describe the same dimensions and scale.
UNITS.update({'kg/m³':UNITS['kg/m3'], 'kg/m^3':UNITS['kg/m3']})
UNITS.update({'um':('length',1e-6),'μm':('length',1e-6),'µm':('length',1e-6),
              'mm':('length',1e-3),'cm':('length',1e-2)})

from backend.tools.unit_registry import ADDITIONAL_UNITS
UNITS.update(ADDITIONAL_UNITS)

def number(value, positive=False):
    if type(value) not in (int,float) or not math.isfinite(value) or abs(value)>1e100 or (positive and value<=0):
        raise ValueError('数值必须有限，且符合正值/范围要求')
    return float(value)


def composition(formula):
    if not isinstance(formula,str) or not formula.strip() or len(formula)>120:
        raise ValueError('请提供明确化学式')
    if re.search(r"(?:[A-Z][a-z]?|\))0(?:[A-Z()]|$)",formula):raise ValueError("原子数不能为零")
    c=Composition(formula)
    if not c or any(not isinstance(e,Element) or not math.isfinite(n) or n<=0 or n>1e6 for e,n in c.items()):
        raise ValueError('化学式必须由真实元素和正的原子数构成')
    return c


def arithmetic(expression, variables=None):
    if not isinstance(expression,str) or len(expression)>500:raise ValueError('表达式过长')
    expression=expression.replace('×','*').replace('÷','/').replace('^','**').replace('−','-')
    tree=ast.parse(expression,mode='eval')
    if len(list(ast.walk(tree)))>100:raise ValueError('表达式过于复杂')
    binary={ast.Add:operator.add,ast.Sub:operator.sub,ast.Mult:operator.mul,ast.Div:operator.truediv,ast.Pow:operator.pow}
    functions={'sqrt':math.sqrt,'log':math.log,'log10':math.log10,'exp':math.exp,'sin':math.sin,'cos':math.cos,'tan':math.tan,'abs':abs}
    def visit(n):
        if isinstance(n,ast.Constant):return number(n.value)
        if isinstance(n,ast.Name):
            if variables and n.id in variables:return number(variables[n.id])
            if n.id in ('pi','e'):return getattr(math,n.id)
        if isinstance(n,ast.UnaryOp) and isinstance(n.op,(ast.UAdd,ast.USub)):
            return number(visit(n.operand)*(1 if isinstance(n.op,ast.UAdd) else -1))
        if isinstance(n,ast.BinOp) and type(n.op) in binary:
            a,b=visit(n.left),visit(n.right)
            if isinstance(n.op,ast.Pow) and abs(b)>1000:raise ValueError('指数过大')
            return number(binary[type(n.op)](a,b))
        if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id in functions and len(n.args)==1 and not n.keywords:
            return number(functions[n.func.id](visit(n.args[0])))
        raise ValueError('只支持数字、四则运算、幂及白名单数学函数')
    return visit(tree.body)


def run(operation, inputs):
    if operation=='energy_per_formula':
        c=composition(inputs['formula']);v=number(inputs['value'])*c.num_atoms
        return {'value':v,'unit':'eV/fu','formula':inputs['formula'],'atoms_in_given_formula':c.num_atoms,'expression':f"{inputs['value']} * {c.num_atoms}"},'仅将明确给定的eV/atom转为每输入化学式eV/fu；不改变能量物理定义，不自动称作形成能。'
    if operation=='math':
        return {'value':arithmetic(inputs['expression'],inputs.get('variables')),'expression':inputs['expression']},'按给定表达式计算；角函数采用弧度。'
    if operation=='convert_units':
        from backend.tools.unit_conversion import convert
        return convert(inputs, UNITS, composition)
    if operation=='composition':
        c=composition(inputs['formula'])
        return {'formula':c.reduced_formula,'molar_mass_g_mol':float(c.weight),'atoms_in_given_formula':c.num_atoms,
                'atomic_fractions':{str(e):float(c.get_atomic_fraction(e)) for e in c.elements},
                'weight_fractions':{str(e):float(c.get_wt_fraction(e)) for e in c.elements}},'质量分数为0–1比例；摩尔质量对应输入化学式，不是自动约分后的式子。'
    if operation=='crystal':
        if 'matrix_angstrom' in inputs:
            if any(k in inputs for k in ('a','b','c','alpha','beta','gamma')):raise ValueError('晶格矩阵与晶格参数二选一')
            matrix=np.asarray(inputs['matrix_angstrom'],dtype=float)
            if matrix.shape!=(3,3) or not np.isfinite(matrix).all() or np.abs(matrix).max()>1e5:raise ValueError('晶格矩阵必须为有限3×3数组')
            lattice=Lattice(matrix)
        else:
            a=number(inputs['a'],True);b=number(inputs.get('b',a),True);c=number(inputs.get('c',a),True)
            angles=[number(inputs.get(k,90),True) for k in ('alpha','beta','gamma')]
            if any(t>=180 for t in angles):raise ValueError('角度必须在0–180度之间')
            lattice=Lattice.from_parameters(a,b,c,*angles)
        volume=float(lattice.volume)
        if not math.isfinite(volume) or volume<1e-9:raise ValueError('晶胞退化或体积过小')
        if 'density_g_cm3' in inputs and 'formula_units' in inputs:raise ValueError('已知密度求原子数与给定Z求密度二选一')
        result={'volume_angstrom3':volume,'reciprocal_matrix_2pi_per_angstrom':lattice.reciprocal_lattice.matrix.tolist()}
        if 'formula' in inputs:
            c=composition(inputs['formula']);mass=float(c.weight)
            if 'formula_units' in inputs:
                z=number(inputs['formula_units'],True)
                result.update(formula_units=z,atoms_per_cell=z*c.num_atoms,density_g_cm3=z*mass/(NA*volume*1e-24))
            elif 'density_g_cm3' in inputs:
                z=number(inputs['density_g_cm3'],True)*volume*1e-24*NA/mass
                result.update(formula_units=z,atoms_per_cell=z*c.num_atoms)
            else:raise ValueError('密度/原子数计算还需要Z（formula_units）或密度')
        elif any(k in inputs for k in ('formula_units','density_g_cm3')):raise ValueError('还需要化学式')
        return result,'输入长度Å、角度度；ρ=Z·M/(N_A·V)，V由Å³转为cm³。倒格矢含2π。原子数结果不自动取整，也不假定输入为原胞。'
    if operation=='reaction_energy':
        reactants=inputs['reactants'];products=inputs['products']
        if not all(isinstance(rows,list) and 1<=len(rows)<=20 for rows in (reactants,products)):raise ValueError('反应两侧需提供条目列表')
        def side(rows):
            amounts={};energy=0.
            for row in rows:
                c=composition(row['formula']);coef=number(row['coefficient'],True)
                energy+=coef*number(row['energy_eV_per_formula'])
                for e,n in c.items():amounts[e]=amounts.get(e,0)+coef*n
            return amounts,energy
        ra,re=side(reactants);pa,pe=side(products)
        if any(not math.isclose(ra.get(e,0),pa.get(e,0),rel_tol=1e-8,abs_tol=1e-8) for e in set(ra)|set(pa)):raise ValueError('反应未配平，不能计算反应能')
        delta=pe-re
        return {'reaction_energy_eV':delta,'energy_lowering':delta<0},'ΔE=Σ产物系数×能量−Σ反应物系数×能量，单位eV/所写反应；能量必须共享参考态和计算方法。ΔE<0是能量降低，不能单独证明有限温度焓变或动力学可行。'
    rows=inputs['entries']
    if not isinstance(rows,list) or not 2<=len(rows)<=40:raise ValueError('凸包第一版支持2–40个条目')
    entries=[]
    for i,row in enumerate(rows):
        c=composition(row['formula'])
        if ('energy_eV' in row)==('energy_eV_per_atom' in row):raise ValueError('每个相必须明确一个能量字段：energy_eV或energy_eV_per_atom')
        energy=number(row['energy_eV']) if 'energy_eV' in row else number(row['energy_eV_per_atom'])*c.num_atoms
        entries.append(PDEntry(c,energy,name=str(i)))
    if len({e for entry in entries for e in entry.composition.elements})>4:raise ValueError('第一版最多四元相图')
    pd=PhaseDiagram(entries);result=[]
    for i,entry in enumerate(entries):
        decomposition,ehull=pd.get_decomp_and_e_above_hull(entry)
        result.append({'index':i,'formula':rows[i]['formula'],'energy_above_hull_eV_per_atom':float(ehull),
                       'stable_in_supplied_set':bool(ehull<=1e-8),'decomposition':[{'index':int(e.name),'formula':rows[int(e.name)]['formula'],'atomic_fraction':float(f)} for e,f in decomposition.items()]})
    return {'phases':result},'仅针对给定相集合构建零温能量凸包，需包含全部端元；相分解系数为原子比例。能量必须共享参考态和计算设置。缺少竞争相会低估Ehull，不能当作完整数据库稳定性。'


ALLOWED={
 'energy_per_formula':{'value','formula'},'math':{'expression','variables'},'convert_units':{'value','from_unit','to_unit','molar_basis','temperature_kind','formula','relation'},'composition':{'formula'},
 'crystal':{'matrix_angstrom','a','b','c','alpha','beta','gamma','formula','formula_units','density_g_cm3'},
 'reaction_energy':{'reactants','products'},'phase_stability':{'entries'}}


def validate_input_numbers(inputs, context, operation=None, materials=(), derived=(), variables=None):
    """Trace operands to supplied data, constants or explicit formula atom counts."""
    from backend.agent.numeric_validation import NUMBER, value, CONSTANTS, quantities, user_quantities, equivalent
    supplied=[]
    for q in derived:
        supplied.append((q['value'],'user_given' if q.get('source_type')=='user_given' else 'tool_result' if q.get('source_type')=='tool_fact' else 'calculated',q))
    format_spans=[m.span() for m in re.finditer(r'保留\s*\d+\s*位|\b(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:significant figures|decimal places)',context,re.I)]
    for m in NUMBER.finditer(context):
        if any(a<=m.start()<b for a,b in format_spans):continue
        q=next((q for q in user_quantities(context) if q.get('span',[0,0])[0]<=m.start()<q.get('span',[0,0])[1]),{})
        try:supplied.append((value(m.group()),'user_given',{'text':m.group(),'span':list(m.span()),
                'source':'user','unit':q.get('unit'),'basis':'per_atom' if q.get('unit')=='eV/atom' else 'explicit_unit' if q.get('unit') else 'unspecified'}))
        except (ValueError,OverflowError):pass
    # A named parameter update inherits only its explicitly supplied prior operand.
    for m in re.finditer(r'(?<![A-Za-z])([A-Za-z][A-Za-z0-9_]*)\s*=\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)',context):
        name,old=m[1],float(m[2])
        update=re.search(r'(?<![A-Za-z])'+re.escape(name)+r'\s*(?:只)?(?:翻倍|加倍|变为(?:原来(?:的)?)?([0-9.]+)倍|减半)',context[m.end():])
        if update:
            factor=.5 if '减半' in update[0] else float(update[1]) if update[1] else 2.
            supplied.append((old*factor,'calculated',{'source':'explicit_named_parameter_update','parameter':name,'inputs':[old,factor],'expression':f'{name}={old}*{factor}','output':old*factor}))
    # A percentage supplies its normalized fraction and complementary factor.
    # These operands are derived only from an explicit user percentage; they
    # are not globally trusted decimal constants or a proof of model choice.
    for m in re.finditer(r'(?<![A-Za-z0-9_.])([-+]?(?:\d+(?:\.\d*)?|\.\d+))\s*[%％]',context):
        percent=float(m[1]);fraction=percent/100
        for factor,expression in ((fraction,f'{percent}/100'),(1-fraction,f'1-({percent}/100)'),(1+fraction,f'1+({percent}/100)')):
            supplied.append((factor,'calculated',{'source':'explicit_user_percentage',
                'expression':expression,'inputs':[{'value':percent,'unit':'%','span':list(m.span())}],
                'output':factor,'unit':'','basis':'percentage_factor'}))
    # A supplied power contains both a base operand and an exponent. NUMBER
    # intentionally evaluates the whole power, so retain the explicit base too.
    for m in re.finditer(r'(?<![A-Za-z0-9_.])([-+]?(?:\d+(?:\.\d*)?|\.\d+))\s*(?:\^|\*\*)\s*\{?[-+]?\d+',context):
        supplied.append((float(m[1]),'user_given',{'text':m[1],'normalization':'explicit power base'}))
    # Explicit English integer words in user input are operands, not invented facts.
    words = dict(zip('zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty'.split(), range(21)))
    for m in re.finditer(r'\b(?:'+'|'.join(words)+r')\b',context,re.I):
        if any(a<=m.start()<b for a,b in format_spans):continue
        supplied.append((float(words[m.group().lower()]),'user_given',{'text':m.group(),'normalization':'English integer word'}))
    for result in materials:
        if result.get('status')!='ok':continue
        for row in result.get('candidates',[]):
            for key,v in row.items():
                if type(v) in (float,int):
                    from backend.agent.numeric_validation import PROPERTIES
                    supplied.append((v,'tool_result',{'object':row.get('material_id'),'formula':row.get('formula'),
                        'property':key,'unit':PROPERTIES.get(key,('',))[0],'basis':'per_atom' if key in ('formation_energy_per_atom','energy_above_hull') else 'property_value',
                        'source':'materials_project','material_id':row.get('material_id')}))
    for name,c in CONSTANTS.items():
        if re.search(c['aliases'],context,re.I):
            supplied.append((c['value'],'physical_constant',{'constant':name,'unit':c['unit']}))
            if name=='atomic_mass':supplied.append((c['value']*1000,'physical_constant',{'constant':name,'unit':'g','provider':'scipy.constants'}))
            if name=='k_B':supplied.append((8.617333262145e-5,'physical_constant',{'constant':name,'unit':'eV/K'}))
    for formula in re.findall(r'(?<![A-Za-z])(?:[A-Z][a-z]?\d*){1,12}',context):
        try:
            c=composition(formula)
            supplied.append((float(c.num_atoms),'calculated',{'operation':'composition','formula':formula,'expression':'sum(stoichiometric coefficients)','output':float(c.num_atoms)}))
            if operation=='math':
                for element,count in c.items():
                    supplied.append((float(count),'calculated',{'operation':'composition','formula':formula,
                        'object':formula,'property':str(element)+'_stoichiometric_count','unit':'',
                        'basis':'per_input_formula','source':'pymatgen.Composition',
                        'expression':f'Composition({formula!r})[{str(element)!r}]','output':float(count)}))
        except (ValueError,TypeError):pass
    supplied += [(1.,'math_expression',{'reason':'implicit coefficient'}), (EV_J,'physical_constant',{'constant':'electron_volt_J','unit':'J'}), (NA,'physical_constant',{'constant':'N_A','unit':'mol^-1'})]
    if operation=='math' and re.search(r'虚构|占位|hypothetical|placeholder',context,re.I):
        # Explicit pedagogical A/B formulae are not real MP compositions, but
        # their written stoichiometry still determines normalization counts.
        for token in re.findall(r'(?<![A-Za-z])(?:[A-Z]\d*){2,}(?![A-Za-z])',context):
            counts=[float(n or 1) for n in re.findall(r'[A-Z](\d*)',token)]
            supplied.append((sum(counts),'calculated',{'operation':'symbolic_stoichiometry','formula':token,
                'expression':'sum(explicit placeholder coefficients)','inputs':counts,'output':sum(counts),
                'basis':'per_input_formula','source':'explicit_hypothetical_user_formula'}))
    if re.search(r'立方|cubic|正交|orthorhombic',context,re.I):supplied.append((90.,'math_expression',{'reason':'explicit orthogonal lattice'}))
    # Count only a delimited observation list, never every number in the question.
    scalar=r'[-+−]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?'
    observations=re.compile(r'(?<![A-Za-z0-9_.])'+scalar+r'(?:\s*(?:、|,|，|;|和|与|及)\s*'+scalar+r'){1,}')
    if re.search(r'MAE|RMSE|平均|误差|errors',context,re.I):
        for match in observations.finditer(context):
            vals=[float(x.replace('−','-')) for x in re.findall(scalar,match.group())]
            supplied.append((float(len(vals)),'calculated',{'expression':'len(observations)',
                'inputs':vals,'output':len(vals),'unit':'','basis':'sample_count','source':'user_observation_list',
                'span':list(match.span())}))
            if re.search(r'RMSE|均方根',context,re.I):
                for v,expr in ((sum(x*x for x in vals),'sum(x*x for x in observations)'),(sum(x*x for x in vals)/len(vals),'mean_squared_error')):
                    supplied.append((v,'calculated',{'source':'user_observation_list','expression':expr,'inputs':vals,'output':v}))
                for x in vals:
                    supplied.append((x*x,'calculated',{'value':x*x,'expression':f'{x}**2','inputs':[x],
                        'output':x*x,'basis':'squared_observation','source':'user_observation_list'}))
    # Unit factors are derived from the registry and only exposed when both
    # relevant units occur in this question. They are never a global whitelist.
    from backend.tools.calculation_admission import compact, QUANTITY as TYPED_QUANTITY
    unit_context=compact(context)
    present=[u for u in UNITS if re.search(r'(?<![A-Za-z])'+re.escape(u)+r'(?![A-Za-z0-9])',unit_context)]
    # SI prefix factors are explicit unit normalizations even when the base unit
    # is implicit in a compound physical formula (e.g. microvolt/K in ZT).
    for src in present:
        dim, scale=UNITS[src]
        if scale != 1 and any(d==dim and f==1 for d,f in UNITS.values()):
            supplied.append((scale,'calculated',{'source':'unit_registry','from_unit':src,
                'expression':'normalize to dimension base unit','output':scale,'basis':dim}))
    # A prefixed input is the same measured quantity after normalization,
    # even when its base unit only occurs implicitly in a declared formula.
    typed=[{'value':float(m[1]),'unit':m[2]} for m in TYPED_QUANTITY.finditer(unit_context)]
    for q in typed:
        src=q['unit']
        if src not in UNITS:continue
        dim,scale=UNITS[src]
        base=next((u for u,(d,f) in UNITS.items() if d==dim and f==1),None)
        if base and scale!=1:
            converted_value=q['value']*scale
            supplied.append((converted_value,'calculated',{'source':'unit_registry',
                'from_unit':src,'to_unit':base,'inputs':[q],'expression':f"{q['value']} * {scale}",
                'output':converted_value,'unit':base,'basis':dim}))
    for src in present:
        for dst in present:
            if src==dst or UNITS[src][0]!=UNITS[dst][0]:continue
            factor=UNITS[src][1]/UNITS[dst][1]
            supplied.append((factor,'physical_constant',{'source':'unit_registry','property':'unit_conversion_factor',
                'from_unit':src,'to_unit':dst,'expression':src+' -> '+dst,'unit':dst+'/'+src,'basis':'conversion_relation'}))
    converted=[]
    for q in user_quantities(context):
        src=q['unit']
        if src not in UNITS:continue
        for dst in present:
            if dst==src or UNITS[dst][0]!=UNITS[src][0]:continue
            converted_value=q['value']*UNITS[src][1]/UNITS[dst][1]
            record={'value':converted_value,'unit':dst,'expression':f"convert({q['value']}, {src}, {dst})",
                    'inputs':[q],'output':converted_value,'source':'unit_registry','basis':'explicit_unit',
                    'from_unit':src,'to_unit':dst,'property':'unit_converted_quantity'}
            supplied.append((converted_value,'calculated',record));converted.append(record)
    if operation=='math' and re.search(r'百分比|百分率|百分数|百分之|保持率|保留率|损失率|衰减率|增长率|相对误差|相对偏差|percentage|percent|retention',context,re.I):
        supplied.append((100.,'math_expression',{'source':'percentage_definition',
            'expression':'percentage = dimensionless_ratio * 100','basis':'percent','unit':'%'}))
    # Angle intermediates are derived from the declared angle and the exact
    # degree/radian relation, never from a model's claimed intermediate value.
    if operation=='math' and re.search(r'角|θ|theta|degree|°|度',context,re.I):
        supplied.append((180.,'math_expression',{'source':'angle_definition','expression':'pi radians = 180 degrees','basis':'angle'}))
        angle_re=r'(?P<double>2\s*(?:θ|theta))?\s*(?:=|为|是|:|：)?\s*(?P<value>'+scalar+r')\s*(?:度|°|degrees?)(?![A-Za-z])'
        for m in re.finditer(angle_re,context,re.I):
            angle=float(m['value'].replace('−','-'));angles=[angle]
            if m['double']:angles.append(angle/2)
            for degrees in angles:
                radians=math.radians(degrees)
                for val,expr in [(degrees,'declared angle / 2' if degrees!=angle else 'declared angle'),
                                 (radians,'degrees * pi / 180'),(math.sin(radians),'sin(degrees * pi / 180)'),
                                 (math.cos(radians),'cos(degrees * pi / 180)')]:
                    supplied.append((val,'calculated',{'source':'declared_angle','inputs':[{'value':angle,'unit':'degree','double_angle':bool(m['double'])}],
                        'expression':expr,'output':val,'basis':'angle_intermediate'}))
    for n,category,origin in list(supplied):
        if n<0:supplied.append((abs(n),'calculated',{'expression':'abs('+str(n)+')','input':origin,'output':abs(n)}))
    derived_quantities=[]
    if re.search(r'差值|差多少|相差|相减|difference|能量差',context,re.I):
        qs=user_quantities(context)
        for a in qs[:12]:
            for b in qs[:12]:
                if a is b or a['unit']!=b['unit']:continue
                delta=a['value']-b['value']
                for v in [delta,abs(delta)]:
                    origin={'expression':str(a['value'])+' - '+str(b['value']),'inputs':[a,b],'output':v,'unit':a['unit'],'absolute_value':v==abs(delta)}
                    supplied.append((v,'calculated',origin));derived_quantities.append({'value':v,'unit':a['unit']})
    if re.search(r'密度|density',context,re.I):
        qs=user_quantities(context)
        for a in qs:
            for b in qs:
                target='amu/Å3' if a['unit']=='amu' and b['unit'] in ('Å3','Å^3') else 'g/cm3' if a['unit']=='g' and b['unit'] in ('cm3','cm^3') else None
                if target and b['value']:
                    count=1.;count_origin=None
                    m=re.search(r'(?P<count>\d+|'+ '|'.join(words) +r')\s+(?:atoms|particles)\b[^.;]{0,50}\b(?:each|of mass)\s+' ,context,re.I)
                    if m and a['unit']=='amu':
                        count=float(words[m['count'].lower()]) if m['count'].lower() in words else float(m['count'])
                        count_origin={'value':count,'text':m['count'],'source':'explicit_atom_count'}
                    v=count*a['value']/b['value'];origin={'expression':str(count)+'*'+str(a['value'])+'/'+str(b['value']),'inputs':[a,b]+([count_origin] if count_origin else []),'output':v,'unit':target,'basis':'mass_per_volume'}
                    supplied.insert(0,(v,'calculated',origin));derived_quantities.append({'value':v,'unit':target})
    if operation in ('convert_units','energy_per_formula'):
        supplied_quantities=user_quantities(context)+derived_quantities+converted+[q for q in derived if q.get('unit')]
        for result in materials:
            for row in result.get('candidates',[]) if result.get('status')=='ok' else []:
                from backend.agent.numeric_validation import PROPERTIES
                supplied_quantities.extend({'value':row[k],'unit':u} for k,(u,_) in PROPERTIES.items() if type(row.get(k)) in (float,int))
        if inputs.get('from_unit','eV/atom') in UNITS and supplied_quantities and not any(equivalent(inputs['value'],inputs.get('from_unit','eV/atom'),q['value'],q['unit']) for q in supplied_quantities):
            raise ValueError('换算输入的数值与来源单位不一致')
    # Explicit per-formula energy rows bind a number to its phase and basis.
    # Do not infer energy rows from an arbitrary nearby scalar or band gap.
    energy_sources={}
    if re.search(r'每化学式|总能量|total energ|per formula',context,re.I):
        scalar=r'[-+−]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?'
        pattern=r'(?<![A-Za-z0-9])(?P<formula>(?:[A-Z][a-z]?\d*){1,12})\s*(?:每化学式能量|总能量|能量为|为|=|:|：)\s*(?P<value>'+scalar+r')\s*eV(?!/atom)'
        for match in re.finditer(pattern,context):
            try:
                formula=match['formula'];n=composition(formula).num_atoms;v=float(match['value'].replace('−','-'))
                energy_sources[formula]={'energy_eV':v,'energy_eV_per_atom':v/n,'n_atoms':n}
            except (ValueError,TypeError):continue
        # An explicitly ordered list binds each energy to its own formula.
        # Equal numbers elsewhere in the question are not interchangeable facts.
        formula_re=r'(?:[A-Z][a-z]?\d*){1,12}'
        delimiter=r'\s*(?:、|,|，)\s*'
        ordered=(r'(?P<formulas>'+formula_re+r'(?:'+delimiter+formula_re+r')+)的?'
                 r'每化学式(?:单位)?能量分别为\s*(?P<values>'+scalar+r'(?:'+delimiter+scalar+r')+)\s*eV(?!/atom)')
        for match in re.finditer(ordered,context):
            formulas=re.split(delimiter,match['formulas']);values=re.split(delimiter,match['values'])
            if len(formulas)!=len(values) or len(set(formulas))!=len(formulas):
                raise ValueError('相与每化学式能量列表无法一一对应')
            for formula,text in zip(formulas,values):
                n=composition(formula).num_atoms;v=float(text.replace('−','-'))
                energy_sources[formula]={'energy_eV':v,'energy_eV_per_atom':v/n,'n_atoms':n}
    records=[]
    source_types={'user_given':'user_input','tool_result':'tool_fact','physical_constant':'trusted_constant',
                  'calculated':'verified_derived_result','math_expression':'trusted_constant'}
    def enrich(record):
        origin=record['origin'];record['source_type']=source_types[record['category']]
        record.update(object=origin.get('object',origin.get('material_id')),property=origin.get('property'),
                      unit=origin.get('unit'),basis=origin.get('basis'),source=origin.get('source',origin.get('constant','user' if record['category']=='user_given' else 'deterministic_rule')))
        return record
    def check(v,path,object_formula=None):
        if type(v) in (int,float):
            field=path.rsplit('.',1)[-1]
            if (operation=='phase_stability' and field=='energy_eV_per_atom' and not energy_sources
                    and re.search(r'每化学式|per formula',context,re.I)):
                raise ValueError('无法确认每化学式能量到每原子能量的绑定；请用energy_eV保留原总能量')
            if object_formula and energy_sources and field in ('energy_eV','energy_eV_per_atom','energy_eV_per_formula'):
                known=energy_sources.get(object_formula)
                key='energy_eV' if field=='energy_eV_per_formula' else field
                if known is None or not math.isclose(float(v),known[key],rel_tol=1e-4,abs_tol=1e-12):
                    raise ValueError('相能量与用户提供的材料/计量基准不一致：'+path)
                origin={'object':object_formula,'property':field,'value':v,'unit':'eV/atom' if key=='energy_eV_per_atom' else 'eV',
                        'basis':'per_atom' if key=='energy_eV_per_atom' else 'per_input_formula','source':'user_phase_energy',
                        'expression':'total_energy / n_atoms' if key=='energy_eV_per_atom' else 'user supplied total energy',
                        'inputs':{'total_energy':known['energy_eV'],'n_atoms':known['n_atoms']},'output':v}
                records.append(enrich({'path':path,'value':v,'category':'calculated' if key=='energy_eV_per_atom' else 'user_given','origin':origin}));return
            hits=[(n,category,origin) for n,category,origin in supplied if math.isclose(float(v),n,rel_tol=1e-4,abs_tol=0)]
            expected_unit='eV/atom' if path.endswith('.energy_eV_per_atom') else 'eV' if path.endswith(('.energy_eV','.energy_eV_per_formula')) else None
            hits=[h for h in hits if not (h[1]=='math_expression' and h[2].get('reason')=='implicit coefficient' and operation!='math' and field!='coefficient')]
            hits=[h for h in hits if not (expected_unit and h[2].get('ref') and h[2].get('unit')!=expected_unit)]
            hits=[h for h in hits if h[1]!='tool_result' or
                  ((not object_formula or h[2].get('formula')==object_formula) and
                   (not expected_unit or h[2].get('unit')==expected_unit))]
            hit=hits[0] if hits else None
            if not hit:raise ValueError('计算参数缺少可追踪来源：'+path+'='+str(v))
            records.append(enrich({'path':path,'value':v,'category':hit[1],'origin':hit[2]}))
        elif isinstance(v,dict):
            object_formula=v.get('formula',object_formula)
            for key,x in v.items():
                if key=='variables':continue
                if key=='expression':
                    # AST constants preserve unary signs and exponent roles.
                    tree=ast.parse(x.replace('×','*').replace('÷','/').replace('^','**').replace('−','-'),mode='eval')
                    def visit(node):
                        if isinstance(node,ast.Name) and node.id in (variables or {}):
                            record=variables[node.id]
                            records.append(enrich({'path':path+'.expression.variable.'+node.id,'value':record['value'],
                                'category':'calculated','origin':record}));return
                        if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.USub,ast.UAdd)) and isinstance(node.operand,ast.Constant):
                            n=float(node.operand.value)*(-1 if isinstance(node.op,ast.USub) else 1)
                            if any(math.isclose(n,t[0],rel_tol=1e-4,abs_tol=0) for t in supplied):check(n,path+'.expression');return
                            check(abs(n),path+'.expression');return
                        if isinstance(node,ast.BinOp) and isinstance(node.op,ast.Pow):
                            # A conversion written as 10**(-24) is one typed
                            # conversion relation, not a free base-10 operand.
                            try:power=arithmetic(ast.unparse(node))
                            except (ValueError,SyntaxError,OverflowError):power=None
                            relation=next((x for x in supplied if x[2].get('source')=='unit_registry' and power is not None and math.isclose(power,x[0],rel_tol=1e-12,abs_tol=0)),None)
                            if relation:
                                records.append(enrich({'path':path+'.expression.conversion','value':power,'category':relation[1],'origin':relation[2]}));return
                            visit(node.left)
                            exponent=node.right
                            sign=1
                            if isinstance(exponent,ast.UnaryOp) and isinstance(exponent.op,(ast.USub,ast.UAdd)):
                                sign=-1 if isinstance(exponent.op,ast.USub) else 1;exponent=exponent.operand
                            if isinstance(exponent,ast.Constant) and type(exponent.value) in (int,float):
                                records.append(enrich({'path':path+'.expression.exponent','value':sign*exponent.value,'category':'math_expression','origin':{'reason':'explicit mathematical exponent','basis':'exponent'}}))
                            else:visit(node.right)
                            return
                        if (operation == 'math' and isinstance(node, ast.BinOp)
                                and isinstance(node.op, ast.Div)
                                and isinstance(node.right, ast.Constant)
                                and type(node.right.value) in (int, float)
                                and node.right.value == 100):
                            # Only an explicitly supplied percentage divided by 100.
                            # Do not trust arbitrary expressions or all literal 100s.
                            operand = None
                            if isinstance(node.left, ast.Name) and node.left.id in (variables or {}):
                                operand = variables[node.left.id]['value']
                            elif isinstance(node.left, (ast.Constant, ast.UnaryOp)):
                                try: operand = arithmetic(ast.unparse(node.left))
                                except (ValueError, TypeError): pass
                            percentages = [float(m[1]) for m in re.finditer(
                                r'(?<![A-Za-z0-9_.])([-+]?(?:\d+(?:\.\d*)?|\.\d+))\s*[%％]', context)]
                            if operand is not None and any(math.isclose(operand, p, rel_tol=1e-12, abs_tol=0) for p in percentages):
                                visit(node.left)
                                records.append(enrich({'path':path+'.expression.percent_denominator',
                                    'value':100, 'category':'math_expression',
                                    'origin':{'source':'percentage_definition', 'basis':'percent',
                                              'expression':ast.unparse(node), 'input_percent':operand}}))
                                return
                        if isinstance(node,ast.Constant):check(node.value,path+'.expression');return
                        for child in ast.iter_child_nodes(node):visit(child)
                    visit(tree)
                else:check(x,path+'.'+key,object_formula)
        elif isinstance(v,list):
            for i,x in enumerate(v):check(x,path+'['+str(i)+']',object_formula)
    check(inputs,'inputs')
    return records


def calculate(operation, inputs, input_provenance=None):
    started=perf_counter();result=None
    try:
        from backend.tools.calculation_schema import validate_request
        validate_request({'operation':operation,'inputs':inputs}, allow_references=False)
        if len(json.dumps(inputs))>16000:raise ValueError('计算输入过长')
        output,assumptions=run(operation,inputs)
        result={'status':'ok','operation':operation,'inputs':inputs,'result':output,'assumptions':assumptions,'source':'Python / pymatgen deterministic calculation',
                'provenance':{'inputs':input_provenance or [],'expression':output.get('expression') or inputs.get('expression') or operation,
                              'operation':operation,'output':output,'engine':'backend.tools.calculation.run','deterministic':True}}
    except (ValueError,KeyError,TypeError,ZeroDivisionError,OverflowError,SyntaxError,QhullError) as exc:
        result={'status':'invalid_query','operation':operation,'inputs':inputs,'message':str(exc)}
    finally:
        if result is not None:
            path=ROOT/'logs/calculation_calls.jsonl';path.parent.mkdir(exist_ok=True)
            with path.open('a') as f:f.write(json.dumps({'timestamp':datetime.now(timezone.utc).isoformat(),'latency_seconds':round(perf_counter()-started,4),**result},ensure_ascii=False)+'\n')
    return result


CALCULATE_TOOL={'type':'function','function':{'name':'calculate',
 'description':'执行确定性科学计算，不心算、不写Python代码。JSON参数的数值字段必须是已算好的数字，不可写 -20/3 等算式；所有算式只放在 math.inputs.expression 字符串中。A/B等占位元素的已知总能量和参考能求形成能用math，不用phase_stability。已知质量和体积求密度不猜晶格参数，可用convert_units：value=质量/体积，from_unit=amu/Å³，to_unit=g/cm³。operation=math表达式expression（sqrt/log/exp/sin等单参数函数）；convert_units(value,from_unit,to_unit)支持eV/J、meV/atom/eV/atom/kJ/mol、Å/nm/m、g/cm3/kg/m3；composition(formula)给摩尔质量、原子/质量分数；crystal(a,b,c单位Å，alpha/beta/gamma度，或matrix_angstrom，formula及formula_units或density_g_cm3)给体积、密度、原子数、倒格矢；reaction_energy(reactants,products条目formula/coefficient/energy_eV_per_formula)，必须配平；phase_stability(entries条目formula和energy_eV总能或energy_eV_per_atom)，需端元。输入取自用户明确数据/已成功工具结果，不猜能量、晶格或Z。',
 'parameters':{'type':'object','properties':{'operation':{'type':'string','enum':sorted(OPS)},'inputs':{'type':'object','properties':{
   'expression':{'type':'string','description':'用用户原始操作数写完整算式；不要把未执行的中间结果替换成新数字。例如用(a-(b+c))/n，不要先心算b+c再填数字。'},'value':{'type':'number'},'from_unit':{'type':'string'},'to_unit':{'type':'string'},'formula':{'type':'string'},
   **{k:{'type':'number'} for k in ('a','b','c','alpha','beta','gamma','formula_units','density_g_cm3')},
   'matrix_angstrom':{'type':'array','items':{'type':'array','items':{'type':'number'}}},
   'entries':{'type':'array','items':{'type':'object','properties':{'formula':{'type':'string'},'energy_eV':{'type':'number'},'energy_eV_per_atom':{'type':'number'}},'required':['formula'],'additionalProperties':False}},
   **{k:{'type':'array','items':{'type':'object','properties':{'formula':{'type':'string'},'coefficient':{'type':'number'},'energy_eV_per_formula':{'type':'number'}},'required':['formula','coefficient','energy_eV_per_formula'],'additionalProperties':False}} for k in ('reactants','products')}
 },'additionalProperties':False}},'required':['operation','inputs'],'additionalProperties':False}}}


def calculation_answer(results):
    blocks=[]
    def fmt(v):return f'{v:.8g}'
    for n,r in enumerate(results,1):
        if r['status']!='ok':continue
        op=r['operation'];v=r['result'];lines=[]
        if op=='math':lines=[f"{v['expression']} = **{fmt(v['value'])}**"]
        elif op=='convert_units':lines=[f"{fmt(r['inputs']['value'])} {r['inputs']['from_unit']} = **{fmt(v['value'])} {v['unit']}**"]
        elif op=='energy_per_formula':lines=[f"每输入化学式能量：**{fmt(v['value'])} {v['unit']}**；{v['expression']}"]
        elif op=='composition':
            lines=[f"输入化学式：{r['inputs']['formula']}；摩尔质量：**{fmt(v['molar_mass_g_mol'])} g/mol**。",'元素质量分数：'+'；'.join(f"{e} {fmt(x*100)}%" for e,x in v['weight_fractions'].items()),'元素原子分数：'+'；'.join(f"{e} {fmt(x*100)}%" for e,x in v['atomic_fractions'].items())]
        elif op=='crystal':
            labels={'volume_angstrom3':'晶胞体积 (Å³)','density_g_cm3':'密度 (g/cm³)','formula_units':'每胞化学式单元数Z','atoms_per_cell':'每胞原子数'}
            lines=[f"{label}：**{fmt(v[key])}**" for key,label in labels.items() if key in v]
            lines.append('含2π的倒格矢矩阵 (Å⁻¹)：\n```json\n'+json.dumps(v['reciprocal_matrix_2pi_per_angstrom'])+'\n```')
        elif op=='reaction_energy':lines=[f"ΔE = **{fmt(v['reaction_energy_eV'])} eV/所写反应**；"+('产物能量更低。' if v['energy_lowering'] else '产物能量未降低。')]
        else:
            for phase in v['phases']:
                decomposition=' + '.join(f"{fmt(d['atomic_fraction'])} × {d['formula']}" for d in phase['decomposition'])
                lines.append(f"- 条目{phase['index']} · {phase['formula']}：Ehull = **{fmt(phase['energy_above_hull_eV_per_atom'])} eV/atom**；"+('在给定相集合中位于凸包。' if phase['stable_in_supplied_set'] else '高于给定凸包；分解：'+decomposition+'（原子比例）。'))
        blocks.append('**科学计算结果 '+str(n)+'**\n\n'+'\n\n'.join(lines)+'\n\n'+r['assumptions'])
    return '\n\n'.join(blocks)

# Keep the published schema aligned with the request validator. Operation-specific
# mandatory fields and numeric domains are also checked before execution.
_REFERENCE={'type':'object','properties':{'ref':{'type':'string','pattern':r'^(?:result|mp|user)_[1-9]\d*(?:\.(?:[A-Za-z_]\w*|\d+))*$'}},'required':['ref'],'additionalProperties':False}
_NUM_OR_REF={'anyOf':[{'type':'number'},_REFERENCE]}
_fields=CALCULATE_TOOL['function']['parameters']['properties']['inputs']['properties']
for _key in ('value','a','b','c','alpha','beta','gamma','formula_units','density_g_cm3'):_fields[_key]=_NUM_OR_REF
_fields['matrix_angstrom']['items']['items']=_NUM_OR_REF
for _list in ('entries','reactants','products'):
    for _key in ('energy_eV','energy_eV_per_atom','coefficient','energy_eV_per_formula'):
        if _key in _fields[_list]['items']['properties']:_fields[_list]['items']['properties'][_key]=_NUM_OR_REF
_fields['variables']={'type':'object','maxProperties':20,'propertyNames':{'pattern':'^[A-Za-z][A-Za-z0-9_]{0,31}$'},'additionalProperties':_REFERENCE}
CALCULATE_TOOL['function']['description'] += ' 支持Pa/MPa/GPa、Å³/cm³/m³换算。数字字段可用先前成功结果引用，如value={"ref":"result_1.value"}；math可用expression="x*2"、variables={"x":{"ref":"result_1.value"}}。只引用当前请求或后端会话状态中实际列出的result_id及quantities.ref，不可自己声明变量值、猜结果ID或修改其单位/材料基准。数学结果单位未知时，不可直接当有单位的换算输入；应先用convert_units取得有单位的结果。'

CALCULATE_TOOL['function']['description'] += ' energy_per_formula(value,formula)将eV/atom转为每输入化学式的eV/fu，原子数由Composition确定。可直接引用会话列出的mp_编号.属性、user_编号.value与result_编号字段；不可猜引用编号。'

_fields.update(molar_basis={'type':'string','enum':['atoms','formula_units']},temperature_kind={'type':'string','enum':['absolute','difference']})
CALCULATE_TOOL['function']['description'] += ' 用户明确要求数值计算/换算时应执行本工具。热容换算支持eV/(atom K)、J/(mol K)、J/(mol_atoms K)、J/(mol_formula K)及kJ、meV形式。含糊mol须指定molar_basis=atoms或formula_units，跨原子/化学式基准须给formula；不能用单位换算混同Cp与Cv。K与°C须temperature_kind=absolute或difference。'

CALCULATE_TOOL['function']['parameters']['properties']['inputs']['properties']['relation']={'type':'string','enum':['E=k_B*T'],'description':'仅当用户明确指定该关系时填写；缺关系先澄清，不自行补足。'}
