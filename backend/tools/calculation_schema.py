"""Deterministic Calculate argument validation, before provenance or execution."""
import math
import re

REQUIRED={'energy_per_formula':{'value','formula'},'math':{'expression'},'convert_units':{'value','from_unit','to_unit'},
          'composition':{'formula'},'crystal':set(),'phase_stability':{'entries'},
          'reaction_energy':{'reactants','products'}}
NUMERIC={'value','a','b','c','alpha','beta','gamma','formula_units','density_g_cm3',
         'energy_eV','energy_eV_per_atom','coefficient','energy_eV_per_formula'}


def validate_request(args, allow_references=True):
    from backend.tools.calculation import ALLOWED
    if not isinstance(args,dict) or set(args)!={'operation','inputs'}:
        raise ValueError('Calculate 必须包含且只包含 operation 和 inputs 对象')
    op=args['operation'];inputs=args['inputs']
    if not isinstance(op,str) or op not in REQUIRED or not isinstance(inputs,dict):
        raise ValueError('未知 operation 或 inputs 不是对象')
    if set(inputs)-ALLOWED[op] or not REQUIRED[op]<=set(inputs):
        raise ValueError('该 operation 的必填参数缺失或包含未知参数')
    def numeric(v):
        if allow_references and isinstance(v,dict):
            if set(v)=={'ref'} and isinstance(v['ref'],str) and re.fullmatch(r'(?:result|mp|user)_[1-9]\d*(?:\.(?:[A-Za-z_]\w*|\d+))*',v['ref']):return
            raise ValueError('引用必须是后端列出的 result/mp/user 数值记录')
        if type(v) not in (float,int) or not math.isfinite(v) or abs(v)>1e100:
            raise ValueError('数值参数必须是有限数字（不可传字符串或布尔值）')
    def fields(obj):
        for key,v in obj.items():
            if key in NUMERIC:numeric(v)
            elif key in ('expression','formula','from_unit','to_unit','relation'):
                if not isinstance(v,str) or not v.strip() or len(v)>500:raise ValueError('文本参数必须是非空且长度受限的字符串')
    fields(inputs)
    if 'molar_basis' in inputs and inputs['molar_basis'] not in ('atoms','formula_units'):raise ValueError('无效molar_basis')
    if 'temperature_kind' in inputs and inputs['temperature_kind'] not in ('absolute','difference'):raise ValueError('无效temperature_kind')
    if op=='math' and 'variables' in inputs:
        vs=inputs['variables']
        if not isinstance(vs,dict) or len(vs)>20:raise ValueError('variables 必须是最多20项的引用对象')
        for key,v in vs.items():
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,31}',key) or key in ('pi','e','sqrt','log','log10','exp','sin','cos','tan','abs'):
                raise ValueError('无效或保留的变量名')
            if allow_references and not isinstance(v,dict):raise ValueError('variables 只能引用已验证结果，不能自行声明数值')
            numeric(v)
    if op=='crystal':
        if 'matrix_angstrom' in inputs:
            matrix=inputs['matrix_angstrom']
            if any(k in inputs for k in ('a','b','c','alpha','beta','gamma')):raise ValueError('矩阵与晶格参数二选一')
            if not isinstance(matrix,list) or len(matrix)!=3 or any(not isinstance(row,list) or len(row)!=3 for row in matrix):raise ValueError('晶格矩阵必须为3×3数组')
            for row in matrix:
                for x in row:numeric(x)
        elif 'a' not in inputs:raise ValueError('crystal 需要 a 或 matrix_angstrom')
    if op in ('phase_stability','reaction_energy'):
        for key in ('entries',) if op=='phase_stability' else ('reactants','products'):
            rows=inputs[key];limit=40 if key=='entries' else 20
            if not isinstance(rows,list) or not (2 if key=='entries' else 1)<=len(rows)<=limit:raise ValueError('无效的计算条目列表')
            for row in rows:
                allowed={'formula','energy_eV','energy_eV_per_atom'} if key=='entries' else {'formula','coefficient','energy_eV_per_formula'}
                if not isinstance(row,dict) or set(row)-allowed or 'formula' not in row:raise ValueError('无效条目字段')
                if key=='entries':
                    if ('energy_eV' in row)==('energy_eV_per_atom' in row):raise ValueError('每个相必须且只能提供一个能量基准')
                elif not allowed<=set(row):raise ValueError('反应条目缺少参数')
                fields(row)
    return args


def normalize_envelope(args):
    """Lift a misplaced operation only when the envelope is unambiguous.

    No values, expressions, units or operations are inferred. Conflicts and
    unknown siblings remain invalid under validate_request. The caller retains
    the original proposal for audit; permission/provenance still run afterward.
    """
    if (isinstance(args, dict) and set(args) == {'inputs'}
            and isinstance(args['inputs'], dict)
            and isinstance(args['inputs'].get('operation'), str)
            and args['inputs']['operation'] in REQUIRED):
        return {'operation': args['inputs']['operation'],
                'inputs': {k:v for k,v in args['inputs'].items() if k != 'operation'}}, 'lift_nested_operation'
    return args, None
