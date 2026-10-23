"""Single bounded v2 runtime for staging and experiments. No router or RAG loop."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time
from uuid import uuid4

from backend.agent.facts import compact_result, tool_facts
from backend.agent.response_contract import detect_contract
from backend.agent.tools import MATERIALS_TOOL, execute_tool
from backend.agent.numeric_validation import inspect_text, validate_json_answer, validate_tool_result
from backend.model.client import LLMClient, ROOT
from backend.tools.calculation import CALCULATE_TOOL
from backend.tools.calculation_session import CalculationSession

SYSTEM = '准确回答用户的问题，遵守用户指定的输出格式。'

@dataclass(frozen=True)
class RuntimeConfig:
    thinking_budget: int = 2048
    core: bool = False
    tools: bool = True
    fact_checks: bool = True
    formatting: bool = True
    max_tool_rounds: int = 2
    max_tokens: int = 4096
    detailed_answers: bool = False
    trace_dir: str = 'logs/v2'

    def __post_init__(self):
        if self.thinking_budget not in range(0, 32769):
            raise ValueError('Invalid thinking budget')
        if not 0 <= self.max_tool_rounds <= 3:
            raise ValueError('Tool round limit must be 0..3')

    @classmethod
    def from_env(cls):
        def flag(name, default):
            value = os.getenv(name, default)
            if value not in ('0', '1'):
                raise ValueError(name + ' must be 0 or 1')
            return value == '1'
        return cls(thinking_budget=int(os.getenv('V2_THINKING_BUDGET', '2048')),
                   tools=flag('V2_TOOLS', '1'), core=flag('V2_CORE', '0'), fact_checks=flag('V2_FACT_CHECKS', '1'),
                   formatting=flag('V2_FORMATTING', '1'), detailed_answers=flag('V2_DETAILED_ANSWERS', '0'), trace_dir=os.getenv('V2_TRACE_DIR', 'logs/v2'))

def make_client(config, request_id, model=None, temperature=0.0):
    return LLMClient(model=model, temperature=temperature, enable_thinking=config.thinking_budget > 0,
                     thinking_budget=config.thinking_budget or None, timeout=90,
                     log_path=str(Path(config.trace_dir) / 'calls' / (request_id + '.jsonl')))

def explicit_calculate(message):
    for match in re.finditer(r'(?:使用|用|调用|通过)\s*Calculate|\buse\s+Calculate\b', message, re.I):
        prefix = message[max(0,match.start()-20):match.start()]
        if not re.search(r'(?:不|别|不要|请勿|无需|无须|不必|禁止|不需要|不得|not|never|without)\s*$',prefix,re.I):
            return True
    return False

def run_answer(message, client, config, *, history=None, active_material=None,
               active_results=None, emit=None, request_id=None, tool_executor=None, core_search=None, session_state=None, knowledge_mode='auto'):
    from backend.agent.session_state import normalize_material_text
    original_message = message
    message = normalize_material_text(message)
    ident = request_id or uuid4().hex
    start = time.perf_counter()
    contract = detect_contract(message)
    from backend.agent.delivery_contract import user_scope, bind_calls, delivery_report, delivery_instruction, result_table, guard_failed_claims, discard_model_db_tables, check_result_scope, failed_calculation_delivery, formatted_failure_delivery, normalize_core_aliases
    scope = user_scope(message) if config.tools else dict(materials=[],properties=[],filters={},must_cover_all=True,explanation="",overflow=False)
    if session_state is not None:
        session_state.begin(message,active_material)
        history=session_state.data['history']
        active_material=session_state.data['active']
        from backend.agent.delivery_contract import inherit_scope
        scope=inherit_scope(scope,session_state.data.get('last_material_scope'))
    # Same system text across experiment arms: only the schemas/budget differ.
    system = SYSTEM
    if config.detailed_answers and contract.kind == 'prose':
        from backend.agent.answer_style import EXPLANATION_POLICY
        system += '\n'+EXPLANATION_POLICY
    if contract.instruction():
        system += '\n'+contract.instruction()
    from backend.agent.specific_claims import POLICY as SPECIFIC_POLICY, SOFTWARE, check_specific_names
    if SOFTWARE.search(message) or re.search(r'未公开|PRIVATE|RC-SAMPLE|论文.*(?:温度|压力|时间|实验条件)',message,re.I):
        system += '\n'+SPECIFIC_POLICY
    from backend.agent.calculation_intent import requires_calculation
    from backend.tools.execution_contract import prepare, text_task, target_report, PER_FORMULA
    pending=session_state.data.get('pending_calculation') if session_state is not None else None
    admission=prepare(message,pending,session_state.data['user_records'] if session_state else (),
                      session_state.quantities() if session_state else ()) if config.tools else {'status':'NOT_REQUESTED','request':None,'pending':None,'permission':{'state':'ALLOW'}}
    restricted=admission['permission']['state']!='ALLOW' or admission['status'] in ('NEEDS_CLARIFICATION','UNSUPPORTED_RELATION','INVALID_INPUT')
    conceptual=text_task(message) if restricted else ''
    calculation_disabled=restricted
    admission['target_operations']=(['math','math'] if (re.search(r'MAE',message,re.I) and re.search(r'RMSE',message,re.I)) or re.search(r'两(?:条|种).{0,10}(?:组合|路径)',message) else ['convert_units'] if re.search(r'换算|转换|换成|转成|转为',message) else ['math'])
    if re.search(r'原子数(?!值)|组成(?!守恒)|摩尔质量',message):admission['target_operations']=['composition']
    if admission.get('canonical_prefix'):admission['target_operations']=['convert_units','math']
    if admission.get('request'):admission['target_operations']=[admission['request']['operation']]
    if calculation_disabled:
        system += '\n本轮执行契约：用户禁止计算或换算。只解释定义、符号和适用条件，不代入数据、不主动新增数值推算或数值例子；取消请求只确认取消。Calculate不可调用。'
    calculation_context=admission.get('execution_context',message)
    if (session_state is not None and session_state.data['calc_records'] and
            re.search(r'刚才|沿用|其他(?:条件)?不变|其余不变|翻倍|减半',message)):
        previous=session_state.data['calc_records'][-1]
        if previous.get('execution',{}).get('success') and previous.get('user_context'):
            calculation_context=previous['user_context']+'\n本轮用户更新：'+message
    required_calculation = config.tools and not calculation_disabled and admission['status']=='READY' and (explicit_calculate(calculation_context) or requires_calculation(calculation_context) or bool(admission.get('request')))
    if session_state is not None:
        session_state.data['pending_calculation']=admission.get('pending')
    if required_calculation:
        system += ('\n用户明确要求数值计算或换算：先取得真实calculate工具结果，再按用户格式回答。计量基准、物理量或必要输入不明确时先指出缺口，不猜。'
                   '不能用心算冒充工具执行。若需数据库输入，先查询数据库。')
    if active_material:
        system += '\n用户已选材料：' + json.dumps(active_material, ensure_ascii=False)
    if active_results:
        system += '\n用户已选筛选结果：' + json.dumps(active_results, ensure_ascii=False)
    if session_state is not None:
        if not restricted: system += session_state.prompt()
        if session_state.data['mp_records'] or session_state.data['calc_records'] or session_state.data.get('last_tool_results'):
            from backend.agent.receipt_guard import POLICY
            system += '\n'+POLICY
    messages = [{'role': 'system', 'content': system}, *(history or []),
                {'role': 'user', 'content': ('只回答以下独立的文字解释任务：'+conceptual+'。不要执行或举例演算；不输出任何数值结果。' if restricted and conceptual else message)}]
    available_tools = [MATERIALS_TOOL] if calculation_disabled or admission.get('request') else [MATERIALS_TOOL, CALCULATE_TOOL]
    if restricted:
        # Keep user-provided semantic context (e.g. the meaning of MAE),
        # without inheriting assistant calculations or execution permission.
        prior_users=[m['content'] for m in (history or []) if m['role']=='user'][-3:]
        if prior_users:messages[0]['content']+='\n仅供术语与任务指代的历史用户背景，不构成本轮计算许可：'+json.dumps(prior_users,ensure_ascii=False)
        messages=[messages[0],messages[-1]]
        messages[0]['content'] += '\n计算子任务状态：'+admission['status']+'。只完成独立文字解释，澄清与等待提示由后端附加；不代入任何数据，不新增数值例子。'
    core_sources = []; core_policy_added = False; core_attempted = False
    from backend.agent.core_intent import core_intent
    core_reason = core_intent(message, enhanced=knowledge_mode == 'enhanced') if config.core and config.tools and config.max_tool_rounds > 0 else None
    if config.core and config.tools:
        from backend.agent.core_tool import CORE_TOOL, CORE_POLICY, execute_core, core_payload
        available_tools = [*available_tools, CORE_TOOL]
    from backend.agent.specific_claims import capabilities, scope_policy, audit_attribution
    capability_policy = ('当前可执行工具能力（由实际schema生成）：'+json.dumps(capabilities(available_tools),ensure_ascii=False)+
        '。只能按列出的filters和operations描述本站能力；返回属性不等于支持筛选。未开放的能力不要编参数，也不要把外部API能力当本站已支持。')
    if config.tools and re.search(r'schema|filters|operation|接口|工具能力|参数|支持',message,re.I):
        messages[0]['content'] += '\n'+capability_policy
    if re.search(r'Materials\s*Project|\bMP\b|数据库|不查库|查库',message,re.I):
        messages[0]['content'] += '\n'+scope_policy([])
    executor = tool_executor or execute_tool
    records = []; events = []; numbers = []; calls = []; answer = ''; error = None
    streamed = False
    calculation_repair = False
    prior_database = re.search(r'Materials\s*Project|\bmp-\d+\b', message, re.I)
    session = CalculationSession(session_state.quantities(), session_state.data['calc_counter']) if session_state is not None else CalculationSession()
    clarification=False
    deterministic_calculation=False
    simple_calculation=not re.search(r'并(?:解释|查询|比较|计算)|同时(?:解释|查询|比较)|然后|再乘|再除|再加|再减|另外|解释|说明|为什么|比较|查询|查一下|Materials Project|mp-\d+',message,re.I)
    if config.tools and restricted and not conceptual:
        answer=admission['message'];clarification=True
    elif config.tools and not restricted and admission.get('request'):
        # Canonical arguments were extracted from user input, not model prose.
        planned=admission['request'];canonical=admission['canonical_context']
        result=session.execute(planned,canonical,authorization=admission)
        record={'id':'calculate-admitted-'+ident,'name':'calculate','arguments':planned,'initiator':'runtime_admission',
                'result':result,'evidence_id':'CALC1','latency':time.perf_counter()-start}
        records.append(record);deterministic_calculation=simple_calculation
        if result.get('status')=='ok':
            if session_state is not None:session_state.accept_calc(result)
            v=result['result'];out=format(v['value'],'.12g')
            if contract.kind=='json':answer=json.dumps({'value':v['value'],'unit':v['unit']},ensure_ascii=False)
            elif contract.kind=='number':answer=out
            elif contract.kind=='tuple':answer='('+out+', '+json.dumps(v['unit'])+')'
            elif contract.kind=='table':answer='|结果|单位|基准|\n|---|---|---|\n|'+out+'|'+v['unit']+'|'+str(v.get('basis',''))+'|'
            else:
                answer='计算结果：**'+out+' '+v['unit']+'**。\n\n表达式：`'+v['expression']+'`。'
                origins={q['object'] for q in result.get('quantities',[]) if q.get('object') and (q.get('source_type')=='tool_fact' or q.get('parents'))}
                if len(origins)==1:answer+='\n\n对应对象：**'+next(iter(origins))+'**（沿用真实回执）。'
                if v.get('basis'):answer+='\n\n结果基准：'+{'atom':'每原子','atoms':'每原子','formula_units':'每化学式单位','absolute':'绝对温度','difference':'温差','declared_equivalent_temperature':'按声明关系定义的等效温度','declared_thermal_energy':'按声明关系计算的能量','mass':'质量'}.get(v['basis'],str(v['basis']))+'。'
                if v.get('basis')=='atoms' and planned['inputs'].get('molar_basis')=='formula_units' and v.get('formula'):
                    from backend.tools.calculation import composition
                    atom_count=float(composition(v['formula']).num_atoms)
                    answer+='\n\n归一化依据：每个 '+v['formula']+' 化学式单位含 '+format(atom_count,'.12g')+' 个原子；先将每摩尔化学式单位的能量换成每化学式单位，再除以 '+format(atom_count,'.12g')+'，得到每原子能量。仅改变计量基准，不改变能量的物理定义。'
                if planned['inputs'].get('relation'):
                    answer+='\n\n仅按用户指定的 E=k_B*T 计算'+('对应能量，不将温度与能量视作一般单位等价。' if v.get('basis')=='declared_thermal_energy' else '等效温度，不表示已测得样品温度。')
            if not simple_calculation:
                messages.extend([{'role':'assistant','content':None,'tool_calls':[{'id':record['id'],'type':'function','function':{'name':'calculate','arguments':json.dumps(planned,ensure_ascii=False)}}]},
                                 {'role':'tool','tool_call_id':record['id'],'content':json.dumps(compact_result(record),ensure_ascii=False)}])
                answer=''
                required_calculation=False
        else:
            admission={**admission,'status':result.get('calculation_state','INVALID_INPUT')};answer=result.get('message','计算未完成。');clarification=True
    if (session_state is not None and contract.kind=='prose' and not session_state.explicit
            and not session_state.data['active'] and len(session_state.data['candidates'])>1
            and re.search(r'它的(?:密度|带隙|空间群|晶系|形成能|Ehull)',message,re.I)
            and not re.search(r'分别|比较|为什么|解释|each|compare',message,re.I)):
        ids=session_state.data['candidates'][:5]
        answer='上一轮有多个结构，尚未选定唯一材料。请指定材料编号（例如 '+ '、'.join(ids)+'），或说明要分别查询哪些条目；我再查询对应属性。'
        clarification=True
    if emit:
        emit('status', {'message': '正在回答…'})
    try:
        if core_reason and not clarification and not deterministic_calculation:
            # Deterministic execution of a declared source-sensitive intent.
            # This is a program receipt, not a claimed model function call.
            t = time.perf_counter()
            pre_call = {'id':'core-prefetch-'+ident,'name':'search_core_knowledge',
                        'arguments':{'query':message},'initiator':'runtime_policy'}
            core_attempted = True
            try:
                pre_result = execute_core(pre_call, search=core_search)
            except Exception as exc:
                pre_result = {'status':'error','message':type(exc).__name__+': '+str(exc),'sources':[],'evidence':[]}
            core_sources.extend(pre_result['sources'])
            payload = core_payload(pre_result)
            record = {**pre_call,'result':pre_result,'evidence_id':'CORE1','latency':time.perf_counter()-t,
                      'model_payload_bytes':len(json.dumps(payload,ensure_ascii=False).encode()),
                      'audit_result_bytes':len(json.dumps(pre_result,ensure_ascii=False).encode())}
            records.append(record)
            messages.extend([{'role':'assistant','content':None,'tool_calls':[{'id':pre_call['id'],'type':'function',
                              'function':{'name':pre_call['name'],'arguments':json.dumps(pre_call['arguments'],ensure_ascii=False)}}]},
                             {'role':'tool','tool_call_id':pre_call['id'],'content':json.dumps(payload,ensure_ascii=False)},
                             {'role':'system','content':CORE_POLICY+'\n当前请求日期：'+datetime.now(timezone.utc).date().isoformat()}])
            available_tools = [t for t in available_tools if t['function']['name']!='search_core_knowledge']
            core_policy_added = True
            if emit:emit('status',{'message':'search_core_knowledge：'+pre_result['status']})
        for turn in range(0 if clarification or deterministic_calculation else config.max_tool_rounds + 1):
            options = {'max_tokens': config.max_tokens}
            if config.tools and turn == config.max_tool_rounds:
                # Omitting schemas alone did not reliably stop tool calls after
                # tool messages on this endpoint. Explicitly close the tool phase.
                options.update(tools=available_tools, tool_choice='none')
            if config.tools and turn < config.max_tool_rounds:
                options.update(tools=available_tools, tool_choice='auto', parallel_tool_calls=True)
                if calculation_repair:
                    options['tools'] = [CALCULATE_TOOL]
                # Narrow execution contract, common to all tool-enabled arms.
                if turn == 0 and not prior_database and required_calculation and not client.enable_thinking:
                    options['tool_choice'] = {'type': 'function', 'function': {'name': 'calculate'}}
            if emit and hasattr(client, 'request_stream'):
                # Each generation attempt has its own draft; tool preambles do
                # not concatenate with the final answer. No second routing call.
                streamed = True
                emit('reset', {'message': '正在生成回答，完成后核对工具与格式…'})
                response = client.request_stream(messages, lambda text: emit('delta', {'text': text}), **options)
            else:
                response = client._request('runtime_v2', messages, **options)
            calls.append({'turn': turn, 'tool_choice': options.get('tool_choice'),
                          'exposed_tools': [t['function']['name'] for t in options.get('tools', [])],
                          'finish_reason': response.get('finish_reason')})
            if response.get('finish_reason') == 'length':
                error = 'generation_length_limit'
            from backend.agent.delivery_contract import explicit_lookup
            if (config.tools and turn < config.max_tool_rounds and not response.get('tool_calls')
                    and explicit_lookup(scope) and not any(r['name']=='search_materials' for r in records)):
                # The user requested this exact scope. A draft table cannot
                # replace execution; backend arguments still pass the normal
                # binding, authorization and receipt validators below.
                events.append({'type':'explicit_lookup_not_executed', 'action':'execute_user_scope',
                               'original_draft':response.get('content') or ''})
                response={**response, 'content':None, 'tool_calls':[{
                    'id':'scope-lookup-'+ident, 'name':'search_materials',
                    'initiator':'backend_user_scope', 'arguments':{
                        'material_query':scope['materials'][0], 'properties':scope['properties'],
                        'filters':scope['filters']}}]}
            if not response.get('tool_calls'):
                if (turn == 0 and required_calculation and not admission.get('request') and not prior_database
                        and config.max_tool_rounds >= 2 and not error):
                    # One bounded repair for an explicit user execution request.
                    # This consumes the existing round budget; ordinary Direct
                    # answers never enter this path. No guessed tool arguments.
                    messages.append({'role':'assistant','content':response.get('content') or ''})
                    messages.append({'role':'system','content':
                        '执行契约尚未满足：上一轮没有调用calculate。请现在按工具schema提交calculate调用；'
                        '不能把上一轮心算当作执行结果。参数无法确认时明确说明，不得假称执行。'})
                    calculation_repair = True
                    continue
                answer = response.get('content') or ''
                break
            if not config.tools or turn >= config.max_tool_rounds:
                error = 'unexpected_tool_call'; break
            validated_calls=[]
            for proposed in response['tool_calls']:
                call=dict(proposed)
                if not isinstance(call.get('arguments'),dict):
                    raw_arguments=call.get('arguments')
                    try:
                        args=json.loads(raw_arguments) if isinstance(raw_arguments,str) else None
                    except (ValueError,TypeError):
                        args=None
                    call['original_arguments']=raw_arguments
                    call['arguments']=args if isinstance(args,dict) else {}
                    if not isinstance(args,dict):
                        call['scope_error']='工具参数必须为合法JSON对象；未执行，也不求值或猜测修复参数。'
                if call.get('name') == 'calculate' and not call.get('scope_error'):
                    from backend.tools.calculation_schema import normalize_envelope
                    normalized, normalization = normalize_envelope(call['arguments'])
                    if normalization:
                        call['requested_arguments'] = call['arguments']
                        call['arguments'] = normalized
                        events.append({'type':'calculation_envelope_normalized', 'action':'shape_only',
                                       'call_id':call['id'], 'normalization':normalization})
                validated_calls.append(call)
            tool_calls = bind_calls(validated_calls, scope, records)
            if not tool_calls:
                messages.append({'role':'system','content':'明确查询范围已经执行过；不要重复调用，请交付已取得结果与未完成状态。'})
                continue
            messages.append({'role': 'assistant', 'content': response.get('content'), 'tool_calls': [
                {'id': c['id'], 'type': 'function', 'function': {'name': c['name'],
                 'arguments': json.dumps(c['arguments'], ensure_ascii=False)}} for c in tool_calls]})
            for call in tool_calls:
                t = time.perf_counter()
                # Bounded execution, even if an API returns an excessive batch.
                if len(records) >= 8:
                    result = {'status': 'error', 'message': 'Request tool execution limit reached'}
                else:
                    try:
                        if call.get('scope_error'): raise ValueError(call['scope_error'])
                        if call['name']=='calculate' and (restricted or admission.get('request')):
                            raise ValueError('Backend execution contract veto: model cannot execute or replace this request')
                        if call['name'] not in [x['function']['name'] for x in available_tools]:
                            raise ValueError('Tool not enabled in v2')
                        if call['name'] == 'search_core_knowledge':
                            if core_attempted:
                                result = {'status':'error','message':'Core已尝试一次；不重复搜索，回答已有证据和一般知识。','sources':[],'evidence':[]}
                            else:
                                core_attempted = True
                                result = execute_core(call, offset=len(core_sources), search=core_search)
                            core_sources.extend(result['sources'])
                        else:
                            tool_message=calculation_context if call['name']=='calculate' else message
                            if session_state is not None and call['name']=='search_materials':
                                from backend.agent.session_state import LABELS
                                query=call['arguments'].get('material_query','')
                                if len(session_state.explicit)==1 and session_state.explicit[0] in LABELS:
                                    target=session_state.explicit[0];label=LABELS[target]
                                    if query==target or label['name'].lower() in query.lower() or label['zh'] in query or query==label['formula']:
                                        call={**call,'requested_arguments':call['arguments'],'arguments':{**call['arguments'],'material_query':target}}
                                        query=target
                                if not session_state.authorize(query):
                                    result={'status':'ambiguous','message':'没有唯一选中材料或请求与当前对象冲突，请指定材料编号，或明确分别比较候选。','candidates':[], 'candidate_ids':session_state.data['candidates']}
                                else:
                                    tool_message=message+'\n后端确认允许查询：'+query
                                    if scope['filters']:
                                        operators={'lt':'<','lte':'<=','gt':'>','gte':'>='}
                                        tool_message+='\n用户明确且沿用的筛选条件：'+ '；'.join(p+operators[op]+str(v) for p,bounds in scope['filters'].items() for op,v in bounds.items())
                                    result=None
                            else:result=None
                            if result is None:result = executor(call, tool_message, active_material=active_material, client=client,
                                              numeric_context=(session_state.materials() if session_state is not None else [])+[r['result'] for r in records if r['name'] == 'search_materials'],
                                              calculation_session=session)
                        result, checks = validate_tool_result(call, result)
                        events.extend(checks)
                        result, scope_checks = check_result_scope(call, result)
                        events.extend(scope_checks)
                        if session_state is not None:
                            if call['name']=='search_materials':session_state.accept_mp(result)
                            if call['name']=='search_materials' and result.get('status') in ('ok','success') and scope['materials']:
                                session_state.data['last_material_scope']=scope
                                rows=result.get('candidates',[])
                                if scope.get('sort_by_ehull'):
                                    rows=sorted(rows,key=lambda row:(row.get('energy_above_hull') is None,row.get('energy_above_hull') or 0,row.get('material_id','')))
                                session_state.data['last_ranked_material_ids']=[row['material_id'] for row in rows[:scope.get('max_rows') or 32]]
                            if call['name']=='calculate':session_state.accept_calc(result)
                            session.add_quantities(session_state.quantities())
                    except Exception as exc:
                        result = {'status': 'error', 'message': type(exc).__name__ + ': ' + str(exc)}
                eid = ('DB' if call['name'] == 'search_materials' else 'CORE' if call['name'] == 'search_core_knowledge' else 'CALC') + str(len(records) + 1)
                record = {**call, 'result': result, 'evidence_id': eid, 'latency': time.perf_counter() - t}
                payload = core_payload(result) if config.core and call['name'] == 'search_core_knowledge' else compact_result(record)
                record['model_payload_bytes'] = len(json.dumps(payload, ensure_ascii=False).encode())
                record['audit_result_bytes'] = len(json.dumps(result, ensure_ascii=False).encode())
                records.append(record)
                messages.append({'role': 'tool', 'tool_call_id': call['id'],
                                 'content': json.dumps(payload, ensure_ascii=False)})
                if emit:
                    emit('status', {'message': call['name'] + '：' + result.get('status', 'error')})
            if session_state is not None:
                from backend.agent.receipt_guard import POLICY
                messages.append({'role':'system','content':POLICY+session_state.prompt()})
            messages.append({'role':'system','content':SPECIFIC_POLICY+'\n'+capability_policy+'\n'+scope_policy(records)+'\n'+contract.instruction()})
            if config.detailed_answers and contract.kind == 'prose' and not any(r['name']=='search_materials' for r in records):
                messages.append({'role':'system','content':
                    '最终呈现：先直接回答，多属性/多候选优先用表格总结，再解释含义和限制。'
                    '只列本次回执确有的对象、数值和性质，不凭印象概括未计算的范围。'
                    '形成能为负仅相对于指定元素参考态；Ehull=0仅在相应计算方法/相集合下位于凸包，不能写绝对稳定或保证合成。'
                    '用解释和适用边界增加内容，不新增未经证据支持的最高/唯一/最佳、温度、阈值等具体事实。'
                    '资料确实支持的陈述在对应句或表格行后引用[E编号]；没有支持就不挂引用。'})
            if config.core and not core_policy_added and any(r['name']=='search_core_knowledge' for r in records):
                messages.append({'role':'system','content':CORE_POLICY+'\n当前请求日期：'+datetime.now(timezone.utc).date().isoformat()})
                available_tools = [t for t in available_tools if t['function']['name']!='search_core_knowledge']
                core_policy_added = True
            if any(r['name']=='search_materials' for r in records) and contract.kind=='prose':
                messages.append({'role':'system','content':delivery_instruction(scope,records)})
                if scope['explanation']:
                    messages.append({'role':'user','content':'数据库表格已由后端负责，你不要输出数据库表格或逐项复述结果。只回答这个独立文字任务：'+scope['explanation']})
        if not answer.strip() and not error:
            error = 'empty_answer'
    except Exception as exc:
        error = type(exc).__name__ + ': ' + str(exc)
    if emit: emit('status', {'message': '正文已生成，正在核对引用、工具数据与格式…'})
    if restricted and conceptual:
        answer=answer+'\n\n**计算子任务：**'+admission['message']
    raw = answer
    # Normalize grouped citation syntax; never invent supporting citations.
    if contract.kind == 'prose':
        answer, alias_events = normalize_core_aliases(answer,core_sources)
        events.extend(alias_events)
        def split_citations(match):
            return ''.join('[' + value + ']' for value in re.findall(r'E\d+', match[0]))
        answer = re.sub(r'\[E\d+(?:\s*[,，;；]\s*E\d+)+\]', split_citations, answer)
    if config.formatting and contract.kind=='json':
        answer,early_format=contract.render(answer)
        if early_format['changes']:events.append({'type':'output_contract_normalization','action':'format_only','changes':early_format['changes']})
    materials = [r['result'] for r in records if r['name'] == 'search_materials']
    validation_materials = (session_state.materials() if session_state is not None else []) + materials
    calculations = [r['result'] for r in records if r['name'] == 'calculate']
    numeric_check = 'not_run'
    if records and config.fact_checks and answer:
        numeric_check = 'structured_binding_only' if contract.kind == 'json' else 'scalar_provenance_audit'
        if contract.kind == 'json':
            answer, changes, numbers = validate_json_answer(answer, validation_materials, calculations)
        else:
            answer, changes, numbers = inspect_text(answer, user_message=message,
                                                    materials=validation_materials, calculations=calculations, sources=core_sources)
        events.extend(changes)
    if answer and contract.kind == 'prose':
        # Core receipt IDs are not in core_payload; only visible source IDs may be cited.
        valid_ids = {r['evidence_id'] for r in records
                     if r['name'] != 'search_core_knowledge'
                     and r['result'].get('status') in ('ok', 'success')}
        valid_ids.update(s['evidence_id'] for s in core_sources)
        def citation(m):
            if m[1] in valid_ids:
                return m[0]
            events.append({'type': 'unknown_citation_id', 'original': m[0], 'action': 'remove_only_invalid_citation'})
            return ''
        answer = re.sub(r'\[((?:DB|CALC|T|CORE|E)\d+)\]', citation, answer)
    if config.fact_checks and answer:
        events.extend(audit_attribution(answer,records))
        answer,corrections=check_specific_names(answer,message,core_sources,contract,allowed_names=[json.dumps(capabilities(available_tools))]);events.extend(corrections)
        from backend.agent.specific_claims import check_definitions
        answer,corrections=check_definitions(answer,contract,unspecified_energy=bool(session_state and session_state.unspecified_energy_refs()));events.extend(corrections)
    if session_state is not None and (records or required_calculation or session_state.data['mp_records']) and answer:
        from backend.agent.receipt_guard import check_receipts
        identities={r['data']['material_id']:r['data'] for r in session_state.data['mp_records'] if session_state.relevant(r['data'])}
        answer,corrections=check_receipts(answer,records,contract,identities,calculation_required=required_calculation);events.extend(corrections)
    # Backend owns DB values/citations. No free-prose identity/value substitution.
    execution_contract=target_report(admission,records,required_calculation or bool(admission.get('request')) or any(r['name']=='calculate' for r in records))
    answer, calculation_delivery_events = failed_calculation_delivery(answer,message,records,execution_contract,scope['explanation'] or text_task(message)) if contract.kind=='prose' else (answer,[])
    events.extend(calculation_delivery_events)
    delivery = delivery_report(scope, records)
    if contract.kind!='prose':
        failure = formatted_failure_delivery(message,contract,records,delivery,execution_contract)
        if failure is not None:
            answer=failure
            events.append({'type':'failed_target_format_envelope','action':'backend_failure_instead_of_guessed_value','custom_success_schema_satisfied':False})
    db_block = result_table(records,scope) if contract.kind=='prose' else ''
    if contract.kind=='prose' and config.tools and delivery['subtasks'] and not db_block:
        db_block='\n\n'.join('**'+t['entity']+'：** 本次未取得该查询的真实回执，不提供数据库结果值。' for t in delivery['subtasks'])
        events.append({'type':'database_target_without_receipt','action':'backend_unresolved_delivery'})
    if db_block:
        # A DB table must not erase a successful dependent calculation target.
        if execution_contract['target_complete']:
            import html
            calc_rows=[]
            for record in records:
                receipt=record['result'];output=receipt.get('result') or {}
                if record['name']=='calculate' and receipt.get('execution',{}).get('success') and type(output.get('value')) in (int,float) and output.get('unit'):
                    calc_rows.append('| '+str(output.get('formula') or '按本轮真实输入')+' | '+format(output['value'],'.12g')+' | '+html.escape(output['unit'])+' | ['+record['evidence_id']+'] |')
            if calc_rows:
                db_block+='\n\n**真实计算回执**\n\n| 对象/输入 | 结果 | 单位 | 来源 |\n|---|---|---|---|\n'+'\n'.join(calc_rows)
        answer, table_events = discard_model_db_tables(answer)
        events.extend(table_events)
        answer, delivery_events = guard_failed_claims(answer, delivery, message)
        events.extend(delivery_events)
        # Model prose never owns DB receipt placement; receipt tables do.
        answer = re.sub(r'\[DB\d+\]', '', answer)
        # No independent explanatory target: deliver receipts directly, rather
        # than a model reconstruction of database values (especially on failure).
        if not scope['explanation'] and not required_calculation:
            answer = db_block
        else:
            answer = db_block+'\n\n'+answer
        if re.search(r'题设|给定|我给|我提供',message):
            from backend.agent.numeric_validation import user_quantities
            given = user_quantities(message)
            if given: answer='**用户原文给定值（非本次数据库结果）：** '+ '；'.join(q['raw'] for q in given)+'。\n\n'+answer
        events.append({'type':'backend_database_delivery','action':'structured_receipt_block','model_draft_preserved_in_raw':True})
    before_format = answer
    formatted, format_report = contract.render(answer)
    if config.formatting:
        answer = formatted
    # Each request has its own client/log. Never mix concurrent requests' tokens.
    llm_records = [json.loads(x) for x in client.log_path.read_text().splitlines()] if client.log_path.exists() else []
    usage = {'input_tokens': 0, 'output_tokens': 0, 'reasoning_tokens': 0}
    for c in llm_records:
        u = c.get('usage') or {}
        usage['input_tokens'] += u.get('prompt_tokens', 0)
        usage['output_tokens'] += u.get('completion_tokens', 0)
        usage['reasoning_tokens'] += (u.get('completion_tokens_details') or {}).get('reasoning_tokens', 0)
    metrics = {'llm_calls': len(llm_records), 'latency': time.perf_counter() - start, **usage}
    execution_contract=target_report(admission,records,required_calculation or bool(admission.get('request')) or any(r['name']=='calculate' for r in records))
    successful_calculation=execution_contract['target_complete']
    task_status = {'calculate_required':required_calculation or bool(admission.get('request')),
                   'calculate_executed':any(o['state']=='SUCCESS' for o in execution_contract['operations']),
                   'explicit_calculation_complete':successful_calculation or execution_contract['target_state']=='NOT_REQUESTED',
                   'calculation_state':execution_contract['target_state'],
                   'calculation_admission':admission,'execution_contract':execution_contract,'delivery_contract':delivery}
    owned_states=[s for s in (delivery['material_target_state'],execution_contract['target_state']) if s!='NOT_REQUESTED']
    delivery['overall_tool_target_state']=('NOT_REQUESTED' if not owned_states else 'SUCCESS' if all(s=='SUCCESS' for s in owned_states) else 'PARTIAL' if any(s in ('SUCCESS','PARTIAL') for s in owned_states) else 'UNRESOLVED')
    delivery['explanation_requested']=bool(scope['explanation'] or conceptual)
    task_status['subtask_permissions']={'EXPLANATION':'ALLOWED' if scope['explanation'] or conceptual else 'NOT_REQUESTED',
                                        'CALCULATION':admission['permission']['state']}
    delivery['explanation_completion']='not_automatically_verified'
    if core_sources and contract.kind == 'prose' and not re.search(r'\[E\d+\]', answer):
        events.append({'type':'retrieved_evidence_not_cited','action':'report_only',
                       'message':'检索到资料但回答未标注引用；不自动把资料挂到未经核对的结论上。'})
    quality = {
        'numeric_check': numeric_check,
        'semantic_support_check': 'not_run',
        'numeric_occurrences_value_matched': sum(n.get('verified') is True for n in numbers),
        'numeric_occurrences_unverified': sum(n.get('verified') is not True and n.get('category') != 'math_expression' for n in numbers),
        'unknown_unit_occurrences': sum(n.get('status') == 'unknown_unit' for n in numbers),
        'background_citations': [s['evidence_id'] for s in core_sources
            if s.get('admission_grade') != 'fact-grade' and re.search(r'\['+s['evidence_id']+r'\]',answer)],
        'note': '数值出现次数不等于事实条数；值匹配不等于对象、性质和条件均已核验。无事件不表示科学正确。',
    }
    trace = {'request_id': ident, 'timestamp': datetime.now(timezone.utc).isoformat(),
             'question': original_message, 'normalized_question': message, 'config': asdict(config), 'model': client.model,
             'temperature': client.temperature, 'actual_thinking': client.enable_thinking,
             'actual_thinking_budget': client.thinking_budget, 'contract': asdict(contract),
             'calls': calls, 'tools': records, 'facts': tool_facts(records), 'numeric_records': numbers,
             'raw_answer': raw, 'validated_answer': before_format, 'answer': answer,
             'validation_events': events, 'format': format_report, 'error': error, 'metrics': metrics,
             'task_status':task_status, 'core_execution_reason':core_reason, 'explicit_calculation_repair':calculation_repair,
             'quality':quality, 'knowledge_mode':knowledge_mode}
    directory = ROOT / config.trace_dir; directory.mkdir(parents=True, exist_ok=True)
    dest = directory / (ident + '.json'); tmp = dest.with_suffix('.tmp')
    tmp.write_text(json.dumps(trace, ensure_ascii=False, indent=2)); tmp.replace(dest)
    db_sources = [{'id': r['evidence_id'], 'status': r['result'].get('status'),
                   'material_query': r['result'].get('material_query'), 'entries': [
                       {'material_id': x['material_id'], 'formula': x.get('formula'),
                        'url': 'https://materialsproject.org/materials/' + x['material_id']}
                       for x in r['result'].get('candidates', [])]} for r in records if r['name'] == 'search_materials']
    if session_state is not None:
        session_state.finish(message,answer,session._count)
        task_status['active_material_id']=(session_state.data['active'] or {}).get('material_id')
        task_status['candidate_ids']=session_state.data['candidates']
        if records:session_state.data['last_tool_results']=[{'name':r['name'],'call_id':r['id'],'status':r['result'].get('status'),'evidence_id':r['evidence_id']} for r in records]
    for source in core_sources:
        source['cited'] = bool(re.search(r'\[E' + str(source['number']) + r'\]', answer))
    task_status['tools'] = [{'name':r['name'],'status':r['result'].get('status'),'evidence_id':r['evidence_id']} for r in records]
    return {'answer': answer, 'raw_answer': raw, 'response_mode': 'clarification' if clarification else 'tools' if records else 'direct',
            'architecture': 'Materials v2 Beta', 'request_id': ident, 'metrics': metrics,
            'sources': core_sources, 'database_sources': db_sources, 'materials': materials,
            'calculations': calculations, 'validation_events': events, 'format': format_report,
            'error': error, 'streamed': streamed, 'tools': records, 'task_status':task_status, 'core_execution_reason':core_reason,
            'quality':quality, 'knowledge_mode':knowledge_mode}

def answer_question(message, client, history=None, active_material=None, active_results=None, emit=None, session_state=None, knowledge_mode='auto'):
    config = RuntimeConfig.from_env(); ident = uuid4().hex
    isolated = make_client(config, ident, model=client.model, temperature=client.temperature)
    result = run_answer(message, isolated, config, history=history, active_material=active_material,
                        active_results=active_results, emit=emit, request_id=ident, session_state=session_state, knowledge_mode=knowledge_mode)
    if result['error'] and not result['answer']:
        raise RuntimeError(result['error'])
    return result
