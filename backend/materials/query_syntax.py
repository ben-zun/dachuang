"""Deterministic textual constraints, distinct from material identities."""
import re
SG = re.compile(r"(?<![A-Za-z0-9])(?:[PABCIFR][a-zA-Z0-9_/−-]*[0-9][a-zA-Z0-9_/−-]*)(?![A-Za-z0-9])")
def space_groups(text):
    # A space group has a conventional lattice prefix and digit/slash/minus.
    return [m.group().strip() for m in SG.finditer(text) if re.search(r'[-−/_]|[a-z]',m.group()) and not re.fullmatch(r'(?:[A-Z][a-z]?\d*)+',m.group())]
def without_space_groups(text):
    for sg in space_groups(text):text=text.replace(sg,' '*len(sg))
    return text
def norm_group(s):return re.sub(r'[ _\s]','',s).replace('−','-').casefold()
def integer(s):
    if s.isdigit():return int(s)
    d=dict(zip('零一二三四五六七八九',range(10)));s=s.replace('两','二')
    if s=='十':return 10
    if '十' in s:
        a,b=s.split('十');return d.get(a,1)*10+d.get(b,0)
    return d.get(s)
def ordinal(text):
    m=re.search(r'第\s*([一二三四五六七八九十两0-9]+)\s*(?:条|项|个)',text)
    if m:return integer(m[1])
    if re.search(r'最后(?:一)?(?:条|项|个)|末项',text):return -1
    if re.search(r'首项|首条',text):return 1
    return None
