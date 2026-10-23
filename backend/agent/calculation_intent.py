"""Require an actual receipt for explicit numeric tasks, without another router call."""
import re


def requires_calculation(text):
    if re.search(r'不要(?:计算|换算|调用)|无需(?:计算|换算)|不必(?:计算|换算)|只(?:讲|解释)(?:原理|方法)|do not (?:calculate|convert)|without calculat', text, re.I):
        return False
    # A material switch followed by an explicit lookup-only instruction is
    # not a unit conversion, even if a negated clause mentions old density.
    positive=re.sub(r'(?:不要|无需|不必|请勿)[^，。；;]*','',text)
    if (re.search(r'换成|改成|改查|切换到',positive) and re.search(r'只(?:查询|查|看)',positive)
            and not re.search(r'计算|换算|转换|折合|算出|转成|转为|\bconvert\b|\bcalculate\b',positive,re.I)):
        return False
    numeric_text = re.sub(r'(?<![A-Za-z0-9])mp-\d+(?!\d)', '', text, flags=re.I)
    numeric = bool(re.search(r'(?<![A-Za-z])[-+]?\d+(?:\.\d*)?', numeric_text))
    request = bool(re.search(r'换算|转换为|转成|换成|折合|统一单位|计算|算出|求出|求(?:无量纲)?[A-Za-zκστ]|是多少|多少|\bconvert\b|\bcalculate\b|\bcompute\b', text, re.I))
    relation = bool(re.search(r'eV|meV|J/|mol|atom|每原子|每化学式|摄氏|开尔文|°C|\bK\b|百分|%|质量|密度|体积|误差|MAE|RMSE|[+*/=]|平方|总能', text, re.I))
    relation = relation or bool(re.search(r'计算|算出|求出|\bcalculate\b|\bcompute\b', text, re.I))
    conceptual = bool(re.search(r'什么是|为什么|含义|定义|what is|why', text, re.I))
    external_operand = bool(re.search(r'换算|转换为|转成|换成|折合|\bconvert\b',text,re.I) and
                            re.search(r'查询|查得|查询结果|真实.*结果|刚才|上一轮|上述',text,re.I))
    return (numeric or external_operand) and request and relation and not (conceptual and not re.search(r'请.*(?:计算|换算)|\bconvert\b|\bcalculate\b', text, re.I))
