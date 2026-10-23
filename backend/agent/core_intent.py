"""Small documented execution rules, not a coverage/confidence classifier."""
import re

# Broad subject families, not a list of individual evaluation questions.
MATERIAL_TOPICS = re.compile(
    r'形成能|凸包|稳定性|热力学|动力学|自由能|熵|焓|化学势|相图|相变|成核|扩散|'
    r'晶体|晶格|晶胞|晶系|空间群|晶界|缺陷|位错|空位|掺杂|固溶|合金|陶瓷|高分子|聚合物|'
    r'能带|带隙|态密度|费米|电子结构|载流子|半导体|绝缘体|金属|超导|热电|铁电|压电|介电|'
    r'磁性|铁磁|反铁磁|自旋|磁各向异性|声子|振动|德拜|热导|比热|弹性|塑性|强度|韧性|疲劳|'
    r'电池|电极|电解质|锂离子|光电|光学|催化|表面|界面|吸附|二维材料|纳米材料|材料|'
    r'第一性原理|密度泛函|赝势|交换关联|分子动力学|机器学习势|材料机器学习|'
    r'(?<![A-Za-z0-9])(?:DFT|PBE|HSE|SCAN|VASP|Phonopy|ASE|LAMMPS|NEB|DOS|MLIP|XRD|XPS|SEM|TEM)(?![A-Za-z0-9])|'
    r'formation\s+energy|convex\s+hull|band\s+gap|phonon|ferroelectri|superconduct|'
    r'thermoelectric|thermodynamic|crystal|semiconductor|diffusion|dislocation|density\s+functional', re.I)
EXPLANATION = re.compile(
    r'什么|讲讲|讲一下|介绍|解释|原理|机理|机制|区别|关系|如何|怎么|为什么|为何|分析|影响|'
    r'用途|应用|特点|比较|对比|适用|局限|限制|条件|判断|意味着|能否|可以.{0,12}吗|'
    r'\b(?:what|why|how|explain|describe|compare|difference|relationship|limitations?|applications?)\b', re.I)

def core_intent(message, enhanced=False):
    # Explicit refusal and other explicit execution contracts take priority.
    if re.search(r'(?:不要|不用|无需|不必|别|不查|without|do not).{0,10}(?:检索|搜索|查资料|资料|文档|Core|RAG|lookup|search)',message,re.I):return None
    if re.search(r'(?:查|检索|搜索|引用|核对|根据|结合|参考|依据).{0,12}(?:Core|资料|文档|综述|来源)|(?:Core|资料|文档|综述).{0,12}(?:查|核对|是否|今天|沿用)',message,re.I):return 'explicit_source_request'
    if re.search(r'教学图示|课件.{0,12}(?:图|示例)',message):return 'explicit_teaching_source'
    if re.search(r'推荐.{0,24}(?:材料|体系|合金)|(?:比较|对比).{0,32}(?:体系|电解质|材料家族)',message):return 'material_family_selection'
    if re.search(r'(?:器件|工艺|方法|工作流|材料).{0,35}(?:实现路线|实现方案|适用工况|局限|产线)',message):return 'method_or_engineering_conditions'
    named=re.findall(r'(?<![A-Za-z0-9])[A-Z][A-Za-z0-9_-]{1,20}(?![A-Za-z0-9])',message)
    if named and re.search(r'结构条件|(?:与|和).{0,30}相界|不能.{0,12}等同|栅结构',message):return 'named_system_conditions'
    if enhanced and MATERIAL_TOPICS.search(message) and EXPLANATION.search(message):
        return 'materials_explanation'
    if enhanced and MATERIAL_TOPICS.fullmatch(message.strip(' ？?。')):
        return 'materials_topic'
    return None
