"""Typed conversions; distinguish molar basis and absolute/difference temperatures."""
import math
import re


def convert(inputs, units, composition):
    value = inputs['value']
    src, dst = inputs['from_unit'], inputs['to_unit']
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('换算输入必须是有限数字')
    # Whitespace is typographic, not a change of physical quantity.
    src, dst = (re.sub(r'\s+', '', u).replace('·', '*') for u in (src, dst))
    temperatures = {'K': (1., 0.), '°C': (1., 273.15), 'degC': (1., 273.15)}
    if src in temperatures and dst in temperatures:
        kind = inputs.get('temperature_kind')
        if kind not in ('absolute', 'difference'):
            raise ValueError('温度换算须明确temperature_kind=absolute或difference')
        offset = temperatures[src][1] - temperatures[dst][1] if kind == 'absolute' else 0.
        if kind == 'absolute' and value + temperatures[src][1] < 0:
            raise ValueError('绝对温度不能低于0 K')
        return {'value': value + offset, 'unit': dst, 'basis': kind,
                'expression': f'{value} + ({offset})', 'conversion_factor': 1., 'conversion_offset': offset}, '绝对温度含零点偏移；温差不加273.15。'
    if src=='K' and dst in ('eV','meV','J'):
        if inputs.get('relation')!='E=k_B*T':raise ValueError('温度到能量须明确关系E=k_B*T')
        factor=1.380649e-23/{'eV':1.602176634e-19,'meV':1.602176634e-22,'J':1.}[dst]
        return {'value':value*factor,'unit':dst,'basis':'declared_thermal_energy',
                'expression':f'{value} * {factor}','conversion_factor':factor}, '仅按声明E=k_B*T计算能量，不将温度和能量视作一般单位等价。'
    if src in ('eV','meV','J') and dst=='K':
        if inputs.get('relation')!='E=k_B*T':
            raise ValueError('能量到温度须由用户明确声明关系E=k_B*T')
        factor={'eV':1.602176634e-19,'meV':1.602176634e-22,'J':1.}[src]/1.380649e-23
        return {'value':value*factor,'unit':'K','basis':'declared_equivalent_temperature',
                'expression':f'{value} * {factor}','conversion_factor':factor}, '仅按用户声明E=k_B*T定义等效温度；不是普通同维度单位换算，也不证明真实样品温度。'
    energy_mol={'J/mol':1.,'kJ/mol':1000.,'J/mol_atoms':1.,'kJ/mol_atoms':1000.,'J/mol_formula':1.,'kJ/mol_formula':1000.}
    if src in energy_mol or dst in energy_mol or src=='eV/fu' or dst=='eV/fu':
        def energy_unit(u):
            if u in energy_mol:
                b='atoms' if 'mol_atoms' in u else 'formula_units' if 'mol_formula' in u else inputs.get('molar_basis')
                if b not in ('atoms','formula_units'):raise ValueError('mol基准尚未确定')
                return b, energy_mol[u]/96485.33212331001
            if u in ('eV/atom','meV/atom'):return 'atoms', .001 if u.startswith('meV')else 1.
            if u=='eV/fu':return 'formula_units',1.
            if u in ('eV','meV'):return inputs.get('molar_basis'), .001 if u=='meV'else 1.
            raise ValueError('摩尔能量换算的目标单位/基准不受支持')
        sb,sf=energy_unit(src);db,df=energy_unit(dst);factor=sf/df
        if sb!=db:
            if not inputs.get('formula'):raise ValueError('跨原子/化学式基准需要明确化学式')
            n=float(composition(inputs['formula']).num_atoms);factor*=n if sb=='atoms'else 1/n
        return {'value':value*factor,'unit':dst,'basis':db,'formula':inputs.get('formula'),
                'expression':f'{value} * {factor}','conversion_factor':factor}, '按已声明摩尔基准换算能量；不自动改变能量物理定义。'
    heat = {'eV/(atomK)': ('atom', 96485.33212331001),
            'meV/(atomK)': ('atom', 96.48533212331001),
            'J/(mol_atomsK)': ('atom', 1.), 'kJ/(mol_atomsK)': ('atom', 1000.),
            'J/(mol_formulaK)': ('formula_units', 1.), 'kJ/(mol_formulaK)': ('formula_units', 1000.)}
    for name in ('J/(molK)', 'kJ/(molK)'):
        if name in (src, dst):
            basis = inputs.get('molar_basis')
            if basis not in ('atoms', 'formula_units'):
                raise ValueError('mol基准未明确：请指定molar_basis=atoms或formula_units')
            heat[name] = ('atom' if basis == 'atoms' else 'formula_units', 1000. if name.startswith('kJ') else 1.)
    if src in heat or dst in heat:
        if src not in heat or dst not in heat:
            raise ValueError('热容只能在同类热容单位之间换算，不能改写Cp/Cv定义')
        sb, sf = heat[src]; db, df = heat[dst]; factor = sf / df
        if sb != db:
            if not inputs.get('formula'):
                raise ValueError('每原子与每化学式基准转换需要明确formula')
            count = float(composition(inputs['formula']).num_atoms)
            factor *= count if sb == 'atom' else 1 / count
        out = value * factor
        return {'value': out, 'unit': dst, 'basis': db, 'formula': inputs.get('formula'),
                'expression': f'{value} * {factor}', 'conversion_factor': factor}, '仅换算单位与声明的计量基准，不把Cp改为Cv，也不把计算结果改称实验值。'
    if src not in units or dst not in units or units[src][0] != units[dst][0]:
        raise ValueError('不支持该单位或维度不一致')
    factor = units[src][1] / units[dst][1]
    basis = 'per_atom' if units[src][0] == 'per_atom' else units[src][0]
    if inputs.get('molar_basis') == 'formula_units' and 'mol' in src + dst and basis == 'per_atom':
        raise ValueError('现有能量mol单位按每摩尔原子；每化学式请先使用energy_per_formula，不能静默换基准')
    return {'value': value * factor, 'unit': dst, 'basis': basis,
            'expression': f'{value} * {factor}', 'conversion_factor': factor}, '按同一物理维度换算单位，不改变物理量定义。'


def validate_source(inputs, context):
    """Bind the literal conversion operand to its supplied unit, not just its digits."""
    from backend.agent.numeric_validation import NUM, value, user_quantities, unit
    wanted = inputs['value']; src = inputs['from_unit']
    if any(math.isclose(q['value'], wanted, rel_tol=1e-10, abs_tol=1e-12) and unit(q['unit']) == unit(src) for q in user_quantities(context)):
        return
    compact = re.sub(r'\s+', '', context).replace('·', '*')
    source = re.sub(r'\s+', '', src).replace('·', '*')
    pattern = re.compile(r'(?<![A-Za-z0-9_.])(' + NUM + r')' + re.escape(source) + r'(?![A-Za-z0-9/])')
    for m in pattern.finditer(compact):
        if math.isclose(value(m[1]), wanted, rel_tol=1e-10, abs_tol=1e-12):return
    raise ValueError('换算输入的数值与from_unit未共同出现在用户数据中；请使用真实带单位结果引用或补充明确输入')



def validate_context(inputs, context):
    """The model cannot establish the requested molar/temperature basis itself."""
    src, dst = inputs['from_unit'], inputs['to_unit']
    if inputs.get('formula') and inputs['formula'] not in context:
        raise ValueError('换算化学式必须来自本题或明确的可信输入，不能自行改换材料')
    generic_mol = any(re.sub(r'\s+', '', u) in ('J/(molK)', 'kJ/(molK)') for u in (src, dst))
    # Preserve an unresolved user molar basis even if a model rewrites the
    # destination as mol_atoms/mol_formula instead of the generic mol alias.
    generic_requested = bool(re.search(r'k?J\s*/\s*\(\s*mol\s*[·*]?\s*K\s*\)', context))
    molar_heat = any('mol' in u and '(' in u for u in (src, dst))
    if generic_mol or (generic_requested and molar_heat):
        if re.search(r'(?:没有|尚未|还没|未).{0,12}(?:确定|明确|说明).{0,20}(?:mol|摩尔|基准)|(?:mol|摩尔|基准).{0,20}(?:未明确|未确定|不明确|不清楚)', context, re.I):
            raise ValueError('用户尚未确认mol基准，不能由模型代选')
        atom = bool(re.search(r'每摩尔原子|mol_atoms|按原子计|mol.{0,12}(?:按|以|指|表示).{0,5}原子', context, re.I))
        formula = bool(re.search(r'每摩尔(?:化学式|[A-Z][a-z]?\d*)|mol_formula|formula_units|mol.{0,12}(?:按|以|指|表示).{0,5}化学式', context, re.I))
        expected = 'atoms' if atom and not formula else 'formula_units' if formula and not atom else None
        explicit_destination = 'atoms' if 'mol_atoms' in dst else 'formula_units' if 'mol_formula' in dst else None
        if generic_requested and explicit_destination and explicit_destination != expected:
            raise ValueError('目标单位别名与用户mol基准不一致')
        if expected is None or inputs.get('molar_basis') != expected:
            raise ValueError('molar_basis与用户声明不一致或尚未确认')
    if src in ('K', '°C', 'degC') and dst in ('K', '°C', 'degC'):
        from backend.tools.calculation_admission import temperature_kind
        expected = temperature_kind(context)
        if expected=='conflict':
            raise ValueError('同题出现绝对温度与温差，请明确本次换算对象')
        if inputs.get('temperature_kind') != expected:
            raise ValueError('temperature_kind与用户温度/温差对象不一致')
