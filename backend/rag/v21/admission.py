"""Block admission overlay. Original documents, vectors and ranking settings stay intact."""
import hashlib,json,re
from pathlib import Path
from backend.rag.v21.common import ROOT

CONFIG=ROOT/'configs/core_admission.json'
GRADES={'fact-grade','background-grade','quarantine'}
def digest(text):return hashlib.sha256(text.encode()).hexdigest()
def screen(block):
    text=block.get('text','');reasons=[]
    if block.get('parse_quality')=='failed':reasons.append('failed_parse')
    if re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ufffd]',text):reasons.append('damaged_control_or_replacement_character')
    if re.search(r'\[(?:unreadable|unparsed|missing)\s+(?:formula|math)',text,re.I):reasons.append('unreadable_formula')
    if len(re.findall(r'[\u2700-\u27bf\ue000-\uf8ff]',text))>=3:reasons.append('corrupted_font_symbols')
    if len(re.findall(r'\ba\d{2,3}\b',text))>=3:reasons.append('font_glyph_codes')
    return ('quarantine' if reasons else 'background-grade'),reasons or ['precision_not_verified']

class AdmissionGate:
    def __init__(self,index_version=None,path=CONFIG):
        self.config=json.loads(Path(path).read_text()) if Path(path).exists() else {'reviews':{}}
        if index_version and self.config.get('index_version',index_version)!=index_version:raise ValueError('Admission reviews belong to another index')
        self.version='admission-'+digest(json.dumps(self.config,sort_keys=True))[:16]
    def grade(self,block):
        grade,reasons=screen(block);review=self.config.get('reviews',{}).get(block['block_id'])
        if review:
            if review.get('text_sha256')!=digest(block['text']):
                return {'grade':'quarantine','reasons':['stale_review_text_changed'],'reviewed':False}
            requested=review.get('grade')
            if requested not in GRADES:raise ValueError('Unknown block admission grade')
            if requested=='fact-grade' and not review.get('precision_verified'):raise ValueError('Fact-grade requires explicit precision verification')
            # A review never overrides obvious current corruption into fact/background.
            if grade!='quarantine' or requested=='quarantine':grade=requested;reasons=review.get('reasons',['recorded_review'])
            return {'grade':grade,'reasons':reasons,'reviewed':True,'review_reference':review.get('review_reference')}
        return {'grade':grade,'reasons':reasons,'reviewed':False}
