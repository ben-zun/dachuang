"""Offline Core V2.1 build. Raw sources are immutable; publication is explicit."""
import argparse
from collections import Counter
import csv
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import time

from backend.rag.v21.common import ROOT, tokens, read_jsonl, write_jsonl
from backend.rag.v21.native import html, markdown

LIBRARY = ROOT / 'data/core_library'
BUILD = ROOT / 'data/core_v21'
SCHEMA = 'core-v21.3'


def sha(value):
    return hashlib.sha256(value).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    tmp.replace(path)


def registry():
    catalog = {s['source_id']: s for s in json.loads((LIBRARY/'catalog.json').read_text())}
    rows = list(csv.DictReader((LIBRARY/'files_manifest.csv').open(encoding='utf-8-sig')))
    seen = set()
    for row in rows:
        if row['status'] != 'downloaded' or row['preferred_for_future_ingestion'].lower() != 'true':
            continue
        if row['role'] != 'content' or row['sha256'] in seen:
            continue
        seen.add(row['sha256'])
        source = catalog[row['source_id']]
        yield {**row, 'doc_id': row['source_id']+'-'+row['sha256'][:16],
               'organization': source['organization_author'], 'source_type': source['source_type'],
               'license': source['license'], 'doi': source.get('doi'), 'scope': source['scope'],
               'source_version': source['year_last_updated'], 'source_sha256': row['sha256']}


def native(path):
    return markdown(path.read_text()) if path.suffix=='.md' else html(path.read_text())


_CONVERTER = None

def pdf(path, target):
    global _CONVERTER
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    if _CONVERTER is None:
        options = PdfPipelineOptions(do_ocr=False, do_table_structure=True, document_timeout=240)
        options.accelerator_options.num_threads = 4
        options.heading_hierarchy_options.enabled = True
        _CONVERTER = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
    result = _CONVERTER.convert(path)
    if result.status.value != 'success':
        raise ValueError('Docling status: '+str(result.status))
    doc = result.document
    save(target.with_suffix('.docling.json'), doc.export_to_dict())
    sections, headings, blocks, images = [], [], [], []
    def flush():
        if blocks:
            sections.append({'section_path': headings[:] or ['Overview'], 'heading': headings[-1] if headings else 'Overview', 'blocks': blocks[:]})
            blocks.clear()
    for item, _ in doc.iterate_items():
        label = item.label.value
        prov = [{'page': p.page_no, 'bbox': p.bbox.model_dump(mode='json')} for p in getattr(item, 'prov', [])]
        if label in ('title','section_header'):
            flush()
            headings = headings[:max(0,getattr(item,'level',1)-1)]+[item.text]
            continue
        if label in ('page_header','page_footer'):
            continue
        if label == 'picture':
            images.append({'item_ref': item.self_ref, 'provenance': prov})
            continue
        text = item.export_to_markdown(doc=doc) if label == 'table' else getattr(item,'text','')
        if not text.strip():
            if label == 'formula': blocks.append({'kind':'math','text':'[unreadable formula]', 'parse_quality':'failed','provenance':prov,'item_ref':item.self_ref})
            continue
        blocks.append({'kind': {'formula':'math','table':'table','caption':'caption','list_item':'list','code':'code'}.get(label,'paragraph'),
                       'text':text.strip(), 'item_ref':item.self_ref, 'provenance':prov})
    flush()
    return sections, images


def parse(row, force=False):
    target = BUILD/'documents'/f"{row['doc_id']}.json"
    if target.exists() and not force:
        old = json.loads(target.read_text())
        if old.get('schema') == SCHEMA and old['source_sha256'] == row['sha256']:
            return old
    path = LIBRARY/row['path']
    if sha(path.read_bytes()) != row['sha256']:
        raise ValueError('source checksum mismatch')
    target.parent.mkdir(parents=True,exist_ok=True)
    sections, images = pdf(path,target) if row['format']=='pdf' else native(path)
    if row['source_id'].startswith('R') and row['format']!='pdf':
        abstract=next((i for i,s in enumerate(sections) if s['heading'].lower()=='abstract'),None)
        if abstract is not None:sections=sections[abstract:]
    blocks=[]
    for si, section in enumerate(sections):
        for bi, block in enumerate(section['blocks']):
            text=block['text'].strip()
            if re.search(r'^(?:references|bibliography|acknowledg\w*|author contributions|copyright)',section['heading'],re.I):continue
            kind=block['kind']
            quality=block.get('parse_quality','text_only')
            # Automated checks are a screen, never a claim of human verification.
            issues=[]
            if '\ufffd' in text or '[unreadable formula]' in text:
                quality='uncertain';issues.append('unreadable_symbols')
            if kind in ('math','table','caption') and row['format']=='pdf':
                quality='uncertain';issues.append('visual_or_formula_review_required')
            ident=f"{row['doc_id']}:s{si}:b{bi}"
            blocks.append({**block,'block_id':ident,'section_id':f"{row['doc_id']}:s{si}",
                           'section_path':section['section_path'],'anchor':section.get('anchor'),
                           'text':text,'parse_quality':quality,'qa_issues':issues,
                           'locator':block.get('item_ref') or (f"line:{block['line_start']}" if block.get('line_start') else block.get('dom_id') or f'section[{si}].blocks[{bi}]'),
                           'provenance':block.get('provenance',[]),'ordinal':len(blocks)})
    for i,b in enumerate(blocks):
        b['previous']=blocks[i-1]['block_id'] if i else None
        b['next']=blocks[i+1]['block_id'] if i+1<len(blocks) else None
        # Nearby dependencies are preserved, not claimed to be semantically resolved.
        b['context_candidates']=[blocks[j]['block_id'] for j in (i-1,i+1) if 0<=j<len(blocks) and blocks[j]['section_id']==b['section_id']]
    if sum(len(b['text']) for b in blocks)<100:
        raise ValueError('no substantive text; manual/OCR review required')
    document={**row,'schema':SCHEMA,'parser':'docling' if row['format']=='pdf' else 'native-dom-markdown',
              'parser_version':importlib.metadata.version('docling') if row['format']=='pdf' else SCHEMA,
              'blocks':blocks,'images':images,'qa_status':'automated_screen_only', 'parsed_at':time.time()}
    save(target,document)
    return document


def split_text(text, maximum=650):
    """Prefer intact blocks; split oversized paragraphs at sentence/line boundaries."""
    if tokens(text)<=maximum:
        return [text]
    pieces=re.split(r'(?<=[.!?。；;])\s+|\n\n+',text)
    if len(pieces)==1:
        pieces=text.splitlines()
    result=[];current=''
    for piece in pieces:
        if tokens(piece)>maximum:
            # Pathological API signatures need a bounded fallback, preserving original block.
            words=piece.split(' ')
            if len(words)==1:words=[piece[i:i+300] for i in range(0,len(piece),300)]
            for word in words:
                if tokens(current+' '+word)>maximum and current:result.append(current);current=''
                current=(current+' '+word).strip()
            continue
        if tokens(current+'\n'+piece)>maximum and current:result.append(current);current=''
        current=(current+'\n'+piece).strip()
    if current:result.append(current)
    return result


def units():
    reviews=json.loads((BUILD/'qa_reviews.json').read_text()) if (BUILD/'qa_reviews.json').exists() else {}
    output=[];seen={};stats=Counter();documents=[]
    for path in sorted((BUILD/'documents').glob('*.json')):
        if path.name.endswith('.docling.json'):continue
        doc=json.loads(path.read_text());documents.append(doc['doc_id'])
        for b in doc['blocks']:
            quality=b['parse_quality']
            review=reviews.get(b['block_id'])
            if review and review.get('text_sha256')==sha(b['text'].encode()):quality=review['quality']
            stats[quality]+=1
            if quality in ('uncertain','failed') or b['kind']=='code' or len(b['text'])<45:continue
            if b['text'].endswith('?') and len(b['text'].split())<35:continue
            # Table/formula must remain intact; oversized complex objects await review.
            if b['kind'] in ('math','table') and tokens(b['text'])>1100:stats['oversized_complex']+=1;continue
            if re.fullmatch(r'\[[^\]]+\]\([^)]+\)',b['text']):continue
            if re.search(r'^\s*(?:\d+[.)]?\s*)?[A-Z][a-z-]+,\s*[A-Z]\.',b['text']) and re.search(r'\b(?:19|20)\d{2}\b',b['text']):
                stats['bibliographic_entry']+=1;continue
            normalized=re.sub(r'\s+',' ',b['text']).strip()
            key=(doc['source_sha256'],normalized)  # no cross-method semantic merging
            if key in seen:stats['duplicate_blocks']+=1;continue
            seen[key]=b['block_id']
            for part,text in enumerate(split_text(b['text']) if b['kind'] not in ('table','math') else [b['text']]):
                header=f"{doc['title']}\n{' / '.join(b['section_path'])}"
                ident=b['block_id']+f':p{part}'
                output.append({'chunk_id':ident,'unit_id':ident,'block_id':b['block_id'],'doc_id':doc['doc_id'],
                    'source_id':doc['source_id'],'title':doc['title'],'organization':doc['organization'],
                    'source_type':doc['source_type'],'source_version':doc['source_version'], 'source_sha256':doc['source_sha256'],
                    'source_url':doc['url'],'original_file':doc['path'],'section_path':b['section_path'],
                    'section_id':b['section_id'],'locator':b['locator'],'provenance':b['provenance'],
                    'kind':b['kind'],'text':text,'embedding_text':header+'\n\n'+text,
                    'parse_quality':quality,'reviewed':bool(review),'context_candidates':b['context_candidates'],
                    'permission':'fact' if quality=='good' else 'background','scope':doc['scope'],
                    'authority':None,'topic':doc['source_id']})
    write_jsonl(BUILD/'units.jsonl',output)
    save(BUILD/'reports/units.json',{'units':len(output),'documents':len(documents),'source_ids':sorted({x['source_id'] for x in output}),'block_quality':dict(stats),'qa_note':'text_only is screened text, not expert verification; good requires a matching recorded review'})
    return output


def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['parse','units']);p.add_argument('--source',nargs='+');p.add_argument('--format',choices=['html','md','pdf']);p.add_argument('--limit',type=int);p.add_argument('--force',action='store_true');p.add_argument('--shard',type=int,default=0);p.add_argument('--shards',type=int,default=1)
    args=p.parse_args();BUILD.mkdir(parents=True,exist_ok=True)
    if args.stage=='units':
        print(len(units()));return
    rows=list(registry())
    if not (BUILD/'registry.jsonl').exists():write_jsonl(BUILD/'registry.jsonl',rows)
    selected=[r for r in rows if (not args.source or r['source_id'] in args.source) and (not args.format or r['format']==args.format)]
    if args.limit:selected=selected[:args.limit]
    selected=[r for i,r in enumerate(selected) if i%args.shards==args.shard]
    for n,row in enumerate(selected,1):
        try:
            doc=parse(row,args.force);status={'doc_id':row['doc_id'],'status':'success','blocks':len(doc['blocks']),'format':row['format']}
        except Exception as exc:
            status={'doc_id':row['doc_id'],'status':'failed','format':row['format'],'error':type(exc).__name__+': '+str(exc)[:400]}
        with (BUILD/'parse_runs.jsonl').open('a') as f:f.write(json.dumps(status,ensure_ascii=False)+'\n')
        print(n,len(selected),json.dumps(status,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
