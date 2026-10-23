"""Local execution/identity corrections, never infer a scientific answer."""
import json
import re
from backend.agent.session_state import LABELS

POLICY='''执行状态只以本次真实工具回执为准，禁止自己生成success回执或把失败说成执行成功。
工具超时、empty、ambiguous、error时明确说明未取得的部分；未获取不等于不存在。可继续一般解释，不补该材料的具体值。
只做单位换算的能量不定义为总能/形成能/Ehull，不自设参考零点。未引用的具体数值例子不主动给出。
材料编号、化学式、空间群、晶系和structure_label只能按后台字段表述；未知晶型不自行命名。
保留一般原理和标准公式的解释，但证据未出现的具体API/class名、经验阈值、寿命和合成概率不要主动举例。
Ehull=0仅描述相应方法/相集合的能量关系，不能保证合成或任意温压稳定；未定义能量不自行当成Ehull或总能。'''

def check_receipts(answer, records, contract, identities, calculation_required=False):
    events=[]
    calc=[r for r in records if r['name']=='calculate']
    failed_calc=(bool(calc) or calculation_required) and not any(r['result'].get('status')=='ok' and r['result'].get('execution',{}).get('success') for r in calc)
    if failed_calc:
        def receipt(text):
            try:
                obj=json.loads(text)
            except (ValueError,TypeError):return text
            if isinstance(obj,dict) and obj.get('status') in ('ok','success') and ('operation' in obj or 'execution' in obj):
                obj['status']='error';obj['execution']={'success':False,'attempted':bool(calc)}
                obj['message']='本次Calculate未成功；模型文本不是工具回执。'
                events.append({'type':'false_execution_receipt','action':'replace_execution_status_only'})
                return json.dumps(obj,ensure_ascii=False)
            return text
        if contract.kind=='json':answer=receipt(answer)
        else:
            answer=re.sub(r'```(?:json)?\s*(\{.*?\})\s*```',lambda m:'```json\n'+receipt(m[1])+'\n```',answer,flags=re.S)
            pattern=r'(?:Calculate|calculate|计算工具|工具)(?:工具)?(?:已|已经)?(?:执行|计算)?成功|已成功(?:调用|使用|执行)(?:Calculate|calculate|计算工具)'
            answer,n=re.subn(pattern,'本次Calculate未成功执行',answer)
            if n:events.append({'type':'false_execution_statement','action':'correct_local_statement','count':n})
        # Preserve explicit JSON/FINAL/tuple formats. The structured status and UI
        # warning always expose failure, even when prose is prohibited.
        if contract.kind=='prose' and not re.search(r'失败|未成功|超时|不支持|未能|无法.*(?:工具|计算)',answer):
            answer='本次Calculate未成功执行；以下仅说明原理与方法，不交付未完成目标的推算数值。\n\n'+answer
            events.append({'type':'execution_failure_notice','action':'prepend_status'})
    failures=[r for r in records if r['result'].get('status') not in ('ok','success')]
    if failures and contract.kind=='prose' and not answer.startswith('本次工具状态：'):
        statuses='、'.join(dict.fromkeys(r['name']+'='+r['result'].get('status','error') for r in failures))
        answer='本次工具状态：'+statuses+'；失败部分未取得新结果。以下可继续说明一般原理或已知信息。\n\n'+answer
        events.append({'type':'tool_failure_notice','action':'prepend_status'})
    # Structured identity values belong to the backend, including JSON replies.
    if contract.kind=='json':
        try:
            obj=json.loads(answer)
            def own(value):
                if isinstance(value,list):
                    for item in value:own(item)
                elif isinstance(value,dict):
                    mid=value.get('material_id');row=identities.get(mid)
                    if row:
                        for key in ('formula','space_group','crystal_system','structure_label','structure_label_source'):
                            if key in value and row.get(key) and value[key]!=row[key]:
                                events.append({'type':'identity_field_mismatch','object':mid,'field':key,'action':'replace_from_backend'})
                                value[key]=row[key]
                    for item in value.values():own(item)
            own(obj);answer=json.dumps(obj,ensure_ascii=False)
        except (ValueError,TypeError):pass
    # Free prose identity is audit-only. A replacement near one ID can corrupt
    # a different material in the same sentence. Structured receipts remain owned.
    return answer,events
