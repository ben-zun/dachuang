"""Explicit claim permission; parsing uncertainty alone never grants precision."""
import hashlib
from backend.rag.v21.admission import AdmissionGate,screen

def digest(text):return hashlib.sha256(text.encode()).hexdigest()

class ClaimPolicy:
    def __init__(self,legacy,span_reviews=None):
        self.legacy=legacy
        self.reviews=span_reviews or {}
        for review in self.reviews.values():
            if review['claim_grade'] not in {'FACT','BACKGROUND','DISCOVERY_ONLY','QUARANTINE'}:raise ValueError('Unknown claim grade')
            if review['claim_grade']=='FACT' and not review.get('precision_verified'):raise ValueError('FACT requires explicit precision verification')
        self.version='claim-policy-v3-'+digest(str(sorted(self.reviews)))[:12]

    def classify(self,block):
        reviewed=self.reviews.get(block['block_id'])
        if reviewed:
            if digest(block['text'])!=reviewed['text_sha256']:
                return dict(retrieval_allowed=False,claim_grade='QUARANTINE',reason='stale_review')
            if screen(block)[0]=='quarantine':
                return dict(retrieval_allowed=False,claim_grade='QUARANTINE',reason='damaged_text')
            return dict(retrieval_allowed=reviewed['claim_grade']!='QUARANTINE',claim_grade=reviewed['claim_grade'],reason='source_bound_span_review')
        if block.get('generated_by_vlm'):
            return dict(retrieval_allowed=True,claim_grade='DISCOVERY_ONLY',reason='generated_description_not_source_fact')
        legacy=self.legacy.grade(block)
        if legacy['grade']=='quarantine' or block.get('parse_quality')=='failed':
            return dict(retrieval_allowed=False,claim_grade='QUARANTINE',reason=';'.join(legacy['reasons']))
        if legacy['grade']=='fact-grade' and legacy.get('reviewed'):
            return dict(retrieval_allowed=True,claim_grade='FACT',reason='existing_hash_bound_precision_review')
        if block.get('parse_quality')=='uncertain':
            return dict(retrieval_allowed=True,claim_grade='DISCOVERY_ONLY',reason='unverified_structure_or_formula')
        return dict(retrieval_allowed=True,claim_grade='FACT' if legacy['grade']=='fact-grade' else 'BACKGROUND',reason='legacy_review_or_screen')

    def grade(self,block):
        p=self.classify(block)
        # Adapter for existing context assembler: discovery text cannot become answer evidence.
        grade='quarantine' if p['claim_grade'] in ('DISCOVERY_ONLY','QUARANTINE') else 'fact-grade' if p['claim_grade']=='FACT' else 'background-grade'
        return dict(grade=grade,reasons=[p['reason']],**p)
