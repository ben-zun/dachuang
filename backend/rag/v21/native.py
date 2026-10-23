"""DOM/Markdown structure extraction; no legacy RAG parser dependencies."""
import re
from bs4 import BeautifulSoup, Tag


def clean(text):
    return re.sub(r'[ \t]+',' ',text.replace('\xa0',' ')).strip()


def table_text(node):
    grid={}
    for ri,row in enumerate(node.select('tr')):
        col=0
        for cell in row.find_all(['th','td'],recursive=False):
            while (ri,col) in grid:col+=1
            value=clean(cell.get_text(' ',strip=True)).replace('|','\\|')
            rs=min(100,int(cell.get('rowspan',1)));cs=min(100,int(cell.get('colspan',1)))
            for dr in range(rs):
                for dc in range(cs):grid[ri+dr,col+dc]=value
            col+=cs
    if not grid:return ''
    width=max(c for r,c in grid)+1;height=max(r for r,c in grid)+1
    rows=['| '+' | '.join(grid.get((r,c),'') for c in range(width))+' |' for r in range(height)]
    rows.insert(1,'| '+' | '.join('---' for _ in range(width))+' |')
    return '\n'.join(rows)


def html(text):
    soup=BeautifulSoup(text,'lxml')
    root=soup.select_one('#mw-content-text .mw-parser-output') or soup.select_one('#mw-content-text') or soup.select_one('.mt-content-container') or soup.select_one('article') or soup.select_one('main') or soup.select_one('[role="main"]') or soup.body
    if root is None:raise ValueError('No HTML content root')
    images=[{'url':n.get('src'),'alt':n.get('alt',''),'id':n.get('id')} for n in root.select('img')]
    for node in root.select('.smj-container'):
        value=node.get_text().replace('[math]','').replace('[/math]','')
        marker=soup.new_tag('span');marker['class']=['core-formula'];marker.string='$$'+value+'$$';node.replace_with(marker)
    for node in root.select('math'):
        annotation=node.select_one('annotation[encoding="application/x-tex"]')
        value=node.get('alttext') or (annotation.get_text() if annotation else None)
        if value:
            marker=soup.new_tag('span');marker['class']=['core-formula'];marker.string='$$'+value+'$$';node.replace_with(marker)
        else:
            # Keep MathML, do not flatten fractions/scripts into a misleading expression.
            marker=soup.new_tag('span');marker['class']=['core-formula','unresolved-math'];marker.string=str(node);node.replace_with(marker)
    for node in root.select('script[type^="math/tex"],img'):
        is_script=node.name=='script';alt=node.get_text() if is_script else node.get('alt','')
        if is_script or alt and re.search('math|latex',' '.join(node.get('class',[]))):
            marker=soup.new_tag('span');marker['class']=['core-formula'];marker.string='$$'+alt+'$$';node.replace_with(marker)
    for node in root.select('script,style,nav,aside,footer,.headerlink,.viewcode-link,.toctree-wrapper,.ltx_bibliography,.article-notes,.contrib-group'):
        node.decompose()
    sections=[];headings=[];blocks=[];anchor=None
    def flush():
        if blocks:
            sections.append({'heading':headings[-1] if headings else 'Overview','section_path':headings[:] or ['Overview'],'anchor':anchor,'blocks':blocks[:]})
            blocks.clear()
    def walk(node):
        nonlocal headings,anchor
        if not isinstance(node,Tag):return
        if re.fullmatch('h[1-6]',node.name):
            flush();headings=headings[:int(node.name[1])-1]+[clean(node.get_text(' ',strip=True))];anchor=node.get('id');return
        if node.name=='dt' and node.get('id'):
            flush();headings=headings[:1]+[node['id']];anchor=node['id']
        kind=None;value='';quality='text_only'
        if node.name=='table':kind='table';value=table_text(node)
        elif node.name=='pre':kind='code';value=node.get_text('\n')
        elif 'core-formula' in node.get('class',[]) or 'math' in node.get('class',[]) or 'ltx_equation' in node.get('class',[]):
            kind='math';value=node.get_text();quality='uncertain' if 'unresolved-math' in node.get('class',[]) else 'text_only'
        elif node.name=='figcaption':kind='caption';value=node.get_text(' ',strip=True)
        elif node.name in ('p','dt','dd') and not node.find(['p','table','pre','h1','h2','h3']):
            kind='paragraph';value=node.get_text(' ',strip=True)
            if value.strip().startswith('$$') and value.count('$$')==2:kind='math'
            if node.select('.unresolved-math'):quality='uncertain'
        elif node.name=='li' and not node.find(['p','table','pre','ul','ol']):kind='list';value=node.get_text(' ',strip=True)
        if kind:
            value=clean(value)
            if value:blocks.append({'kind':kind,'text':value,'parse_quality':quality,'dom_id':node.get('id')})
            return
        for child in node.children:walk(child)
    walk(root);flush()
    return sections,images


def markdown(text):
    # Preserve source lines and intact fenced code, display math and tables.
    lines=text.splitlines();headings=[];sections=[];blocks=[];buffer=[];fence=None;start=0
    def block():
        if buffer:
            value='\n'.join(buffer).strip()
            if value:
                kind='code' if value.startswith('```') else 'math' if value.startswith(('$$','\\[')) else 'table' if value.startswith('|') else 'paragraph'
                blocks.append({'kind':kind,'text':value,'line_start':start+1})
            buffer.clear()
    def section():
        if blocks:
            sections.append({'heading':headings[-1] if headings else 'Overview','section_path':headings[:] or ['Overview'],'blocks':blocks[:]});blocks.clear()
    for i,line in enumerate(lines):
        if re.match(r'^> For the complete documentation index|^\{%.*%\}$',line):continue
        heading=re.match(r'^(#{1,6})\s+(.+)$',line)
        if not fence and heading:
            block();section();headings=headings[:len(heading[1])-1]+[heading[2]];continue
        marker='```' if line.strip().startswith('```') else '$$' if line.strip()=='$$' else None
        if marker:
            if fence==marker:buffer.append(line);block();fence=None
            elif not fence:block();start=i;buffer.append(line);fence=marker
            else:buffer.append(line)
            continue
        if not line.strip() and not fence:block();continue
        if not buffer:start=i
        buffer.append(line)
    block();section()
    return sections,[]
