from copy import deepcopy
from backend.tools.materials_project import SEARCH_MATERIALS_TOOL, search_materials, PROPERTIES

MATERIALS_TOOL=deepcopy(SEARCH_MATERIALS_TOOL)
MATERIALS_TOOL['function']['description'] += ' 仅当用户明确要求筛选稳定结构时，未给阈值可用 energy_above_hull:{lte:0}。问某材料是否稳定时只查询属性，不预先筛掉不稳定结构。用户提供数值只需解释时不重复查库；查询事实并解释机制可同时调用 Core。'
KNOWLEDGE_TOOL={'type':'function','function':{'name':'search_knowledge',
 'description':'检索材料概念、理论和计算方法的 Core 背景证据。保留原始问题；只在续问时补齐指代，不自动外网回退，也不决定是否允许回答。',
 'parameters':{'type':'object','properties':{'query':{'type':'string','minLength':1,'maxLength':2000}},'required':['query'],'additionalProperties':False}}}
LITERATURE_TOOL={'type':'function','function':{'name':'search_literature',
 'description':'用户明确要论文、文献、最新研究或研究进展时，检索真实外部论文。query 用英文材料名及主题；可按年份范围和材料科学领域筛选。返回文献线索不代表全文已验证。',
 'parameters':{'type':'object','properties':{'query':{'type':'string'},'year_from':{'type':'integer'},'year_to':{'type':'integer'},'materials_science':{'type':'boolean'},'limit':{'type':'integer','minimum':1,'maximum':10,'description':'用户要求的文献条数，默认5'}},'required':['query'],'additionalProperties':False}}}
from backend.tools.calculation import CALCULATE_TOOL
TOOLS=[MATERIALS_TOOL,KNOWLEDGE_TOOL,LITERATURE_TOOL,CALCULATE_TOOL]



def execute_tool(call, message, active_material=None, client=None, numeric_context=None, calculation_session=None):
    args=call['arguments']
    if call['name']=='calculate':
        from backend.tools.calculation_session import CalculationSession
        session=calculation_session if calculation_session is not None else CalculationSession()
        return session.execute(args,message,materials=numeric_context or ())
    if not isinstance(args,dict):raise ValueError('无效工具参数')
    if call['name'] in ('search_core_knowledge','search_knowledge','search_literature'):
        from backend.agent.v21 import execute
        return execute(call,message,client,active_material)
    if call['name']!='search_materials':raise ValueError('未知工具')
    if set(args)-{'material_query','properties','filters'}:raise ValueError('未知材料工具参数')
    properties=args.get('properties',[])
    if not isinstance(properties,list) or any(p not in PROPERTIES for p in properties):raise ValueError('不支持的材料属性')
    if not isinstance(args.get('filters',{}),dict):raise ValueError('筛选条件必须是对象')
    query=args.get('material_query')
    if not isinstance(query,str) or not query.strip():raise ValueError('材料表达不能为空')
    if query not in message and not (active_material and query in [active_material['material_id'],active_material['formula']]):
        import re
        from backend.materials.resolver import resolve_material
        # Extract original Chinese material spans; never accept arbitrary model rewrites.
        originals=[]
        for chunk in re.findall(r'[\u4e00-\u9fff]{2,30}',message):
            for prefix in ('请查询','请查','查询','查一下','查'):
                if chunk.startswith(prefix):chunk=chunk[len(prefix):];break
            chunk=re.split(r'的|是否|有多|是多少|稳定吗',chunk)[0]
            if chunk and chunk not in originals:originals.append(chunk)
        proposed=resolve_material(query)
        matches=[]
        for original in originals:
            resolved=resolve_material(original)
            if resolved['kind']!='unresolved' and resolved.get('value')==proposed.get('value') and proposed['kind']!='unresolved':
                matches.append(original)
        if len(matches)!=1:raise ValueError('材料表达必须保留用户原文，不能翻译或猜测')
        args={**args,'material_query':matches[0]}

    if active_material and not args.get('filters') and (query==active_material['material_id'] or (query==active_material['formula'] and query not in message)):
        return {'status':'ok','material_query':query,'primary':active_material,'candidates':[active_material],
                'multiple_structures':False,'selected_structure_only':True,'properties':args.get('properties',[])}
    # A general description is not permission to hide alternative structures.
    # Keep the originally proposed filter in the receipt for auditability.
    import re
    proposed_filters=args.get('filters', {})
    filter_text=re.sub(r'(?:不要|无需|不必|不需要|不|do not|without)\s*(?:进行|做)?\s*(?:筛选|filter(?:ing)?)', '', message, flags=re.I)
    filtering_requested=bool(re.search(r'筛选|筛出|寻找|找出|找.*(?:稳定|凸包)|(?:稳定|凸包上).*(?:材料|结构).*(?:有哪些|列表|列出)|只要|仅要|满足|低于|高于|小于|大于|不超过|不少于|至少|至多|[<>≤≥]|\b(?:filter|below|above|less than|greater than)\b',filter_text,re.I))
    if proposed_filters and not filtering_requested:
        args={**args,'filters':{}}
    result=search_materials(**args,client=client)
    if proposed_filters and not filtering_requested:
        result['ignored_unrequested_filters']=proposed_filters
        result['filter_notice']='用户未要求筛选，已按原始材料表达查询全部候选；未采用模型额外提出的阈值。'
    return result
