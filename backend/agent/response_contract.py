"""User output requirements. Formatting never supplies a missing answer."""
from dataclasses import dataclass, asdict
import json
import re

NUMBER=r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?'

@dataclass(frozen=True)
class ResponseContract:
    kind: str='prose'
    final: bool=False
    boxed: bool=False
    only: bool=False

    def instruction(self):
        if self.kind=='prose' and not self.final and not self.boxed:return ''
        if self.kind=='binary':
            return 'Return exactly one line: FINAL: YES or FINAL: NO.' if self.final else 'Return exactly YES or NO.'
        requirements={'json':'Return valid JSON with the fields requested by the user; no Markdown fence.',
            'number':'Return the requested numeric answer, preserving its sign and exponent.',
            'tuple':'Return the tuple in the requested order.',
            'formula':'Return the requested mathematical expression.',
            'table':'Use a Markdown table.'}
        instruction=requirements.get(self.kind,'')
        if self.boxed:instruction+=' End with one LaTeX \\boxed{final answer}; keep units outside the box and reasoning concise.'
        if self.final:instruction+=' Start the final answer line with FINAL:.'
        if self.only:instruction+=' Do not add prose outside the requested format.'
        return instruction.strip()

    def render(self,answer):
        """Normalize presentation only. Invalid/ambiguous input is returned intact."""
        original=answer or '';text=original.strip();changes=[]
        def json_value(t):
            def reject(s):raise ValueError('non-finite JSON number')
            return json.loads(t,parse_constant=reject)
        def tuple_value(t):
            # No parsing/evaluation of formula terms: only validate the container.
            return bool(re.fullmatch(r'\([^\n]+,[^\n]+\)',t))
        def valid(t):
            body=t.strip()
            if self.final:
                m=re.fullmatch(r'FINAL:\s*(.+)',body,re.S)
                if not m:return False
                body=m[1].strip()
            if self.boxed:
                content=boxed_answer(body)
                if not content or body.count(r'\boxed{')!=1:return False
                if self.only:
                    bare=body.removeprefix('$$').removesuffix('$$').strip('$').strip()
                    if bare!=r'\boxed{'+content+'}':return False
                if self.kind=='number':return bool(re.fullmatch(NUMBER,content))
                if self.kind=='tuple':return tuple_value(content)
                return True
            if self.kind=='binary':return body in ('YES','NO')
            if self.kind=='json':
                try:json_value(body);return True
                except (ValueError,TypeError):return False
            if self.kind=='number':return bool(re.fullmatch(NUMBER,body))
            if self.kind=='tuple':return tuple_value(body)
            if self.kind=='table':return bool(re.search(r'\|\s*:?-{3,}',body)) and body.count('\n')>=2
            if self.kind=='formula':return bool(body) and len(body.splitlines())<=3
            return True
        # A successful validator is the identity function, including whitespace.
        if valid(text):
            return original,{'kind':self.kind,'final':self.final,'boxed':self.boxed,
                             'valid':True,'changes':[],'answer_guessed':False}
        fence=re.fullmatch(r'```(?:json|latex|math)?\s*\n(.*?)\n```',text,re.S|re.I)
        if fence and self.kind in ('json','number','tuple','formula'):
            text=fence[1].strip();changes.append('removed_outer_fence')
        if self.kind=='binary':
            match=re.fullmatch(r'(?:FINAL\s*:\s*)?(YES|NO)[.!。]?',text,re.I)
            if not match and self.only:
                match=re.search(r'(?:^|\n)\s*FINAL\s*:\s*(YES|NO)[.!。]?\s*$',text,re.I)
            if match:
                text=('FINAL: ' if self.final else '')+match[1].upper()
                changes.append('normalized_explicit_binary')
        elif self.kind=='tuple' and not self.boxed:
            try:
                array=json_value(text)
                if isinstance(array,list) and len(array)>=2 and all(type(x) in (int,float) for x in array):
                    text='('+', '.join(json.dumps(x) for x in array)+')';changes.append('array_to_explicit_tuple')
            except (ValueError,TypeError):pass
        if self.boxed and not boxed_answer(text):
            bare=text.removeprefix('$$').removesuffix('$$').strip('$').strip()
            final_line=re.search(r'(?:^|\n)(?:FINAL|Answer|Final answer|答案|最终答案)\s*[:：]\s*([^\n]+)\s*$',text,re.I)
            if final_line:
                bare=final_line[1].strip().strip('$').strip()
                if self.only:
                    determined=re.fullmatch('('+NUMBER+r')\s*(?:eV(?:/atom)?|g/cm[³3]|kg/m[³3]|nm|m|K|GPa|g|kg)',bare)
                    if determined:bare=determined[1]
            if re.fullmatch(NUMBER,bare) or tuple_value(bare):
                replacement=r'\boxed{'+bare+'}'
                text=replacement if self.only or not final_line else text[:final_line.start()].rstrip()+'\n'+replacement
                changes.append('boxed_explicit_final_answer' if final_line else 'boxed_standalone_answer')
        if self.final and self.kind!='binary' and not re.match(r'^FINAL:',text):
            # A standalone answer already chosen by the model can receive a label.
            determined=(self.kind=='number' and bool(re.fullmatch(NUMBER,text))) or (self.boxed and bool(boxed_answer(text)))
            if determined:text='FINAL: '+text;changes.append('labelled_standalone_answer')
        accepted=valid(text)
        return (text if accepted else original),{'kind':self.kind,'final':self.final,'boxed':self.boxed,
            'valid':accepted,'changes':changes if accepted else [],'answer_guessed':False}

def boxed_answer(text):
    start=text.rfind(r'\boxed{')
    if start<0:return None
    begin=start+7;depth=1
    for i in range(begin,len(text)):
        if text[i]=='{':depth+=1
        elif text[i]=='}':
            depth-=1
            if not depth:return text[begin:i].strip() or None
    return None

def detect_contract(message):
    # Read only the current user's instruction, never retrieved/tool content.
    only=bool(re.search(r'只(?:输出|返回|用)|不解释|one final line only|do not include|only (?:return|output)|as a number only|nothing else',message,re.I))
    final=bool(re.search(r'(?<![A-Za-z0-9_])FINAL\s*:',message,re.I))
    boxed=bool(re.search(r'\\boxed|inside (?:a |a single )?(?:LaTeX )?box',message,re.I))
    if re.search(r'(?:YES\s*(?:/|or|或|、)\s*(?:FINAL\s*:\s*)?NO)|FINAL\s*:\s*YES',message,re.I):
        return ResponseContract('binary',final,False,only)
    # A conditional tuple instruction is not a request to turn every answer into a tuple.
    shape_message=re.sub(r'For multiple answers, use a tuple[^.]*\.', '',message,flags=re.I)
    request=r'(?:输出|返回|给出|请给|格式|只用|answer|return|output|format|respond|give|put|provide)'
    if re.search(request+r'[^\n。]{0,70}JSON|(?:以|用)\s*JSON\s*(?:给出|作答|回答|返回|输出|表示)|JSON[^\n。]{0,30}(?:对象|格式|only)',message,re.I):kind='json'
    elif re.search(request+r'[^\n。]{0,50}(?:Markdown表格|markdown table|表格|table)',message,re.I):kind='table'
    elif re.search(request+r'[^\n。]{0,50}(?:元组|tuple)',shape_message,re.I):kind='tuple'
    elif re.search(r'只输出[^\n。]{0,30}(?:公式|formula)|only[^\n.]{0,30}formula',message,re.I):kind='formula'
    elif re.search(r'只输出[^\n。]{0,30}(?:数值|数字)|(?:return|output|answer)[^\n.]{0,120}(?:number only|only a number)',message,re.I):kind='number'
    else:kind='prose'
    return ResponseContract(kind,final,boxed,only)
