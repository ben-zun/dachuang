"""Request-local, bounded references to verified deterministic calculations.

This traces arithmetic and typed tool data; it does not prove a physical model.
No global result store, no guessed operands and no generated-code execution.
"""
from copy import deepcopy
from threading import RLock
import ast
import re

from backend.tools.calculation_schema import validate_request

FIELD_UNITS={'volume_angstrom3':'Å³','density_g_cm3':'g/cm³','molar_mass_g_mol':'g/mol',
             'reaction_energy_eV':'eV','energy_above_hull_eV_per_atom':'eV/atom',
             'formula_units':'','atoms_per_cell':'','atoms_in_given_formula':''}
INPUT_UNITS={'a':'Å','b':'Å','c':'Å','alpha':'degree','beta':'degree','gamma':'degree',
             'density_g_cm3':'g/cm³','formula_units':'','coefficient':'',
             'energy_eV':'eV','energy_eV_per_atom':'eV/atom','energy_eV_per_formula':'eV'}


class CalculationSession:
    def __init__(self, quantities=(), counter=0):
        self._records={q['ref']:deepcopy(q) for q in quantities};self._count=counter;self._created=0;self._lock=RLock()

    def add_quantities(self, quantities):
        with self._lock:self._records.update({q['ref']:deepcopy(q) for q in quantities})

    def _resolve(self, ref):
        key=ref if '.' in ref else ref+'.value'
        with self._lock:
            if key not in self._records:raise ValueError('引用不存在或尚未验证成功：'+ref)
            return deepcopy(self._records[key])

    def execute(self,args,context,materials=(),authorization=None):
        from backend.tools.calculation import calculate, validate_input_numbers, run
        request_valid=False;inputs_valid=False;resolved=None;parents=[];bindings={};refs=[]
        from backend.tools.calculation_admission import authorize_call, receipt
        from backend.tools.execution_contract import permission
        permit=permission(context)
        admission=authorization or authorize_call(args,context)
        if permit['state']!='ALLOW':
            admission={'status':'NOT_REQUESTED','message':permit['message']}
        elif authorization and authorization.get('permission',{}).get('state','ALLOW')!='ALLOW':
            admission={'status':'NOT_REQUESTED','message':'本轮无计算许可。'}
        if admission['status']!='READY':
            return {'status':'not_requested' if admission['status']=='NOT_REQUESTED' else 'invalid_query',
                    'calculation_state':admission['status'],'operation':args.get('operation') if isinstance(args,dict) else None,
                    'message':admission['message'],'execution':{'attempted':False,'success':False},
                    'request_validation':{'schema_valid':False,'provenance_valid':False}}
        if authorization and args!=authorization.get('request'):
            raise ValueError('程序准入与实际执行参数不一致')
        try:
            validate_request(args);request_valid=True
            # Normalize only exact, existing receipt references. Attributes
            # remain forbidden to the arithmetic evaluator; no eval/getattr.
            if args['operation']=='math':
                expression=args['inputs']['expression']
                proposed=deepcopy(args);variables=proposed['inputs'].setdefault('variables',{})
                owner=self
                class ReceiptReferences(ast.NodeTransformer):
                    def visit_Attribute(self,node):
                        ref=ast.unparse(node)
                        if not re.fullmatch(r'(?:result|mp|user)_[1-9]\d*(?:\.(?:[A-Za-z_]\w*|\d+))+',ref):
                            raise ValueError('表达式属性只允许精确的可信数值引用')
                        owner._resolve(ref)
                        name='receipt_operand_'+str(len(variables))
                        while name in variables:name+='x'
                        variables[name]={'ref':ref}
                        return ast.copy_location(ast.Name(id=name,ctx=ast.Load()),node)
                tree=ast.parse(expression.replace('×','*').replace('÷','/').replace('^','**').replace('−','-'),mode='eval')
                if any(isinstance(n,ast.Attribute) for n in ast.walk(tree)):
                    proposed['inputs']['expression']=ast.unparse(ReceiptReferences().visit(tree))
                    validate_request(proposed)
                    args=proposed
            op=args['operation']
            def resolve(x,path='inputs',parent=None):
                if isinstance(x,dict):
                    if set(x)=={'ref'}:
                        record=self._resolve(x['ref']);parents.append(record['ref']);refs.append(record)
                        expected=('eV/atom' if op=='energy_per_formula' else args['inputs'].get('from_unit')) if path=='inputs.value' and op in ('convert_units','energy_per_formula') else (None if op=='math' and path.startswith('inputs.variables.') else INPUT_UNITS.get(path.rsplit('.',1)[-1]))
                        if expected is not None:
                            from backend.agent.numeric_validation import unit
                            from backend.tools.calculation import UNITS
                            ru=record.get('unit');same=(ru is not None and unit(ru)==unit(expected)) or (ru in UNITS and expected in UNITS and UNITS[ru]==UNITS[expected])
                            if not same:
                                raise ValueError('引用结果的单位/基准与当前输入不一致；请先明确执行单位转换')
                        if parent and record.get('object') and parent.get('formula') and record.get('formula') and parent['formula']!=record['formula']:
                            raise ValueError('引用结果绑定了其他材料')
                        if path.startswith('inputs.variables.'):
                            bindings[path.split('.')[-1]]=record
                        return record['value']
                    return {k:resolve(v,path+'.'+k,x) for k,v in x.items()}
                if isinstance(x,list):return [resolve(v,path+'.'+str(i),parent) for i,v in enumerate(x)]
                return x
            resolved=resolve(args['inputs'])
            validate_request({'operation':op,'inputs':resolved},allow_references=False)
            with self._lock:derived=deepcopy(list(self._records.values()))
            # References are exact values. Literal fallback is retained for old
            # callers, but every accepted intermediate remains in the audit trace.
            trace=validate_input_numbers(resolved,context,op,materials,derived,bindings)
            if op=='convert_units':
                from backend.tools.unit_conversion import validate_context, validate_source
                validate_context(resolved,context)
                if not isinstance(args['inputs']['value'],dict) and ('(' in resolved['from_unit'] or resolved['from_unit'] in ('K','°C','degC')):
                    validate_source(resolved,context)
            for record in refs:
                trace.append({'path':'reference','value':record['value'],'category':'calculated',
                              'source_type':'verified_derived_result','origin':record,
                              **{k:record.get(k) for k in ('object','property','unit','basis','source')}})
            inputs_valid=True
            result=calculate(op,resolved,input_provenance=trace)
            if result['status']=='ok':
                expected,_=run(op,resolved)
                if expected!=result['result']:raise ValueError('计算输出复算不一致')
                with self._lock:
                    if self._created>=24:raise ValueError('单次请求最多保留24个计算结果')
                    self._count+=1;self._created+=1;ident='result_'+str(self._count)
                    records=[]
                    def collect(x,path='',parent=None):
                        if type(x) in (int,float):
                            name=path.rsplit('.',1)[-1];u=FIELD_UNITS.get(name)
                            if name=='value':u=result['result'].get('unit')
                            record={'ref':ident+'.'+path,'object':None,'property':name,'value':x,
                                    'unit':u,'basis':result['result'].get('basis') if name=='value' and result['result'].get('basis') else 'per_atom' if u=='eV/atom' else 'unknown' if u is None else 'explicit_unit',
                                    'source':'calculate','source_type':'verified_derived_result','operation':op,
                                    'expression':result['result'].get('expression') or resolved.get('expression',op),'inputs':deepcopy(resolved),
                                    'input_provenance':deepcopy(trace),'parents':sorted(set(parents)),'arithmetic_verified':True,'physical_model_verified':False}
                            if parent and parent.get('formula'):record.update(formula=parent['formula'],object=parent['formula'])
                            origins=[o for o in trace if o.get('object')]
                            objects={o['object'] for o in origins}
                            if len(objects)==1:
                                record.update(object=next(iter(objects)),formula=next((o.get('formula') or (o.get('origin') or {}).get('formula') for o in origins if o.get('formula') or (o.get('origin') or {}).get('formula')),record.get('formula')))
                            records.append(record)
                        elif isinstance(x,dict):
                            for k,v in x.items():collect(v,(path+'.' if path else '')+k,x)
                        elif isinstance(x,list):
                            for i,v in enumerate(x):collect(v,path+'.'+str(i),parent)
                    collect(result['result'])
                    for record in records:self._records[record['ref']]=deepcopy(record)
                result.update(result_id=ident,quantities=records)
                result['provenance'].update(parents=sorted(set(parents)),physical_model_verified=False)
        except (ValueError,TypeError,KeyError,OverflowError,SyntaxError) as exc:
            result={'status':'invalid_query','operation':args.get('operation') if isinstance(args,dict) else None,
                    'message':str(exc),'inputs':resolved,'requested_arguments':args}
        result['request_validation']={'schema_valid':request_valid,'provenance_valid':inputs_valid}
        result['execution']={'attempted':inputs_valid,'success':result.get('status')=='ok'}
        result.setdefault('calculation_state','INVALID_INPUT' if result.get('status')=='invalid_query' else 'ERROR')
        return receipt(result,admission)
