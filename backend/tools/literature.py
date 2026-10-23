"""One provider: OpenAlex search + normalized metadata/abstracts, no automatic ingestion."""
import hashlib,json,os,sqlite3,time,threading,re
from datetime import datetime,timezone
import requests
from dotenv import load_dotenv
from backend.paths import ROOT,LITERATURE_DATA as DATA

LOCK=threading.Lock();LAST=0.
def abstract_text(index):
    if not isinstance(index,dict):return None
    words={}
    for word,positions in index.items():
        if not isinstance(word,str) or not isinstance(positions,list):return None
        for p in positions:
            if type(p) is not int or not 0<=p<10000 or p in words:return None
            words[p]=word
    return ' '.join(words[p] for p in sorted(words)) if words else None

def search_literature(query,year_from=None,year_to=None,materials_science=True,limit=5):
    if not isinstance(query,str) or not query.strip() or len(query)>2000:raise ValueError('无效论文检索词')
    if type(materials_science) is not bool or type(limit) is not int or not 1<=limit<=10:raise ValueError('无效论文检索参数')
    current=datetime.now(timezone.utc).year
    for year in (year_from,year_to):
        if year is not None and (type(year) is not int or not 1800<=year<=current):raise ValueError('年份范围无效')
    if year_from and year_to and year_from>year_to:raise ValueError('年份顺序无效')
    filters=['is_retracted:false']
    if materials_science:filters.append('topics.field.id:25')
    if year_from:filters.append(f'from_publication_date:{year_from}-01-01')
    if year_to is None:year_to=current
    if year_to:filters.append(f'to_publication_date:{year_to}-12-31')
    search_query=re.sub(r'\bMLIPs?\b','machine learning interatomic potentials',query.strip(),flags=re.I)
    params={'search':search_query,'filter':','.join(filters),'per_page':min(30,limit*3),'select':'id,doi,title,publication_year,authorships,abstract_inverted_index,open_access,primary_location,cited_by_count,type,topics'}
    key=hashlib.sha256(('literature-v21:'+json.dumps(params,sort_keys=True)).encode()).hexdigest();cache=DATA/'literature_cache.sqlite'
    with sqlite3.connect(cache,timeout=30) as db:
        db.execute('CREATE TABLE IF NOT EXISTS search (key TEXT PRIMARY KEY, value TEXT, expires REAL)')
        row=db.execute('SELECT value FROM search WHERE key=? AND expires>?',(key,time.time())).fetchone()
    if row:return {**json.loads(row[0]),'cache_hit':True,'year_range':[year_from,year_to],'search_query':search_query}
    load_dotenv(ROOT/'.env');headers={'User-Agent':'materials-agent/1.0 academic literature search'}
    if os.getenv('OPENALEX_API_KEY'):headers['Authorization']='Bearer '+os.getenv('OPENALEX_API_KEY')
    global LAST
    try:
        for attempt in range(2):
            with LOCK:
                pause=max(0,1-(time.monotonic()-LAST))
                if pause:time.sleep(pause)
                LAST=time.monotonic()
            response=requests.get('https://api.openalex.org/works',params=params,headers=headers,timeout=(10,25))
            if response.status_code in (429,500,502,503,504) and attempt==0:time.sleep(2);continue
            response.raise_for_status();payload=response.json();break
        if not isinstance(payload.get('results'),list):raise ValueError('Invalid literature payload')
        timestamp=datetime.now(timezone.utc).isoformat();papers=[]
        seen_titles=set();seen_dois=set()
        for work in payload['results']:
            if not work.get('title') or not work.get('id'):continue
            if work.get('type') in ('peer-review','paratext','erratum') or re.match(r'^(?:review|reviewer report)\s+(?:for|of)\b',work['title'],re.I):continue
            if re.search(r'/review\d*(?:$|[/?#])',work.get('doi') or '',re.I):continue
            title_key=re.sub(r'[^a-z0-9]','',work['title'].lower());doi=work.get('doi')
            if title_key in seen_titles or (doi and doi in seen_dois):continue
            seen_titles.add(title_key)
            if doi:seen_dois.add(doi)
            papers.append({'paper_id':work['id'],'title':work['title'],'authors':[a.get('author',{}).get('display_name') for a in work.get('authorships',[]) if a.get('author',{}).get('display_name')],
                'year':work.get('publication_year'),'abstract':abstract_text(work.get('abstract_inverted_index')),'doi':work.get('doi'),
                'url':work.get('doi') or (work.get('primary_location') or {}).get('landing_page_url') or work['id'],'open_access':(work.get('open_access') or {}).get('is_oa'),
                'open_access_url':(work.get('open_access') or {}).get('oa_url'),'citation_count':work.get('cited_by_count'),'retrieved_at':timestamp,'provider':'OpenAlex','evidence_scope':'abstract_only' if work.get('abstract_inverted_index') else 'metadata_only','source_type':work.get('type'),'authority':3 if work.get('type') in ('article','review') and ((work.get('primary_location') or {}).get('source') or {}).get('type')=='journal' and not 'arxiv' in (work.get('doi') or '').lower() else 2,'publication_status':'provider_metadata_not_independently_reviewed'})
        papers=papers[:limit]
        result={'status':'ok' if papers else 'no_results','query':query,'papers':papers,'provider':'OpenAlex','retrieved_at':timestamp,'cache_hit':False,'year_range':[year_from,year_to],'search_query':search_query}
        with sqlite3.connect(cache,timeout=30) as db:db.execute('INSERT OR REPLACE INTO search VALUES (?,?,?)',(key,json.dumps(result,ensure_ascii=False),time.time()+86400))
        return result
    except (requests.RequestException,ValueError,KeyError):
        return {'status':'error','query':query,'papers':[],'provider':'OpenAlex','message':'外部论文检索暂时不可用；没有检索结果不能解释为没有相关研究。'}

def paper_sources(papers):
    result=[]
    for p in papers:
        if not p['abstract']:continue
        n=len(result)+1
        result.append({'number':n,'id':n,'doc_id':p['paper_id'],'paper_id':p['paper_id'],'title':p['title'],'source_type':'paper_abstract','authority':p.get('authority',2),'topic':'external_literature','section':'摘要（未获取全文）','section_path':['摘要（未获取全文）'],'text':p['abstract'],'source_url':p['url'],'url':p['url'],'chunk_ids':[p['paper_id']+'#abstract'],'score':0.,'paper':p,'evidence_scope':'abstract_only'})
    return result
