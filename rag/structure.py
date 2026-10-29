"""Content-aware units over exact corpus Markdown spans; no content cleaning.

Structure/sentence rules, NOT embedding-similarity semantic segmentation.
Flat calendar grouping is a labelled heuristic; raw HTML is not re-read.
"""
import re
from urllib.parse import urlsplit
from .block_render import markdown

MONTH = r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*'
DATE = re.compile(r'\b'+MONTH+r'\s*\d|\b\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)\b|\bAll Day\b',re.I)
EVENT_LINK = re.compile(r'https?://[^\s<>]+/(?:events?|production)/[^\s<>]+|https?://cmu\.libcal\.com/(?:calendar|event)/',re.I)
NUMBER_HEADING = re.compile(r'^[#\s]*[\d,.+%$\s−–-]+$')


def markdown_rows(text):
    """Infer block types from Markdown while keeping exact source offsets."""
    lines=text.splitlines(keepends=True);offsets=[0]
    for line in lines:offsets.append(offsets[-1]+len(line))
    rows=[];i=0
    def special(line):
        return bool(re.match(r"^ {0,3}(?:#{1,6}\s|`{3,}|~{3,}|[-*+]\s|\d+[.)]\s|\|)",line))
    while i<len(lines):
        if not lines[i].strip():
            if rows:rows[-1]['end']=offsets[i+1]
            i+=1;continue
        start=i;line=lines[i];h=re.match(r'^ {0,3}(#{1,6})\s+(.+)',line)
        fence=re.match(r'^ {0,3}(`{3,}|~{3,})(.*)',line)
        if h:
            block={'type':'heading','level':len(h[1]),'text':h[2].strip()};i+=1
        elif fence:
            i+=1
            while i<len(lines):
                closing=re.match(r'^ {0,3}('+re.escape(fence[1][0])+r'{'+str(len(fence[1]))+r',})\s*$',lines[i])
                i+=1
                if closing:break
            block={'type':'code','text':''.join(lines[start:i])}
        elif line.lstrip().startswith('|') and i+1<len(lines) and re.match(r'^\s*\|?\s*:?-{3,}',lines[i+1]):
            i+=2
            while i<len(lines) and lines[i].lstrip().startswith('|'):i+=1
            cells=[{'text':c.strip(),'header':True} for c in line.strip().strip('|').split('|')]
            block={'type':'table','rows':[cells],'caption':''}
        elif re.match(r'^\s*(?:[-*+] |\d+[.)] )',line):
            i+=1
            while i<len(lines) and lines[i].strip() and (re.match(r'^\s*(?:[-*+] |\d+[.)] )',lines[i]) or lines[i].startswith(('  ','\t'))):i+=1
            block={'type':'list'}
        else:
            i+=1
            while i<len(lines) and lines[i].strip() and not special(lines[i]):i+=1
            block={'type':'paragraph','text':''.join(lines[start:i])}
        rows.append({'start':offsets[start] if rows else 0,'end':offsets[i],
                     'block':block,'block_indices':[]})
    if rows:rows[-1]['end']=len(text)
    return rows


def mapped_blocks(doc):
    """Map each top-level block to original Markdown, including separators."""
    text = doc['content_markdown']
    blocks = doc.get('blocks', [])
    if not blocks:return markdown_rows(text)
    found, cursor = [], 0
    for index, block in enumerate(blocks):
        rendered = markdown([block])
        if not rendered:
            continue
        pos = text.find(rendered, cursor)
        if pos < 0 or text[cursor:pos].strip():
            return [{'start':0,'end':len(text),'block':{'type':'paragraph'},'block_indices':[],
                     'mapping_fallback':True}]
        found.append({'start':pos,'end':pos+len(rendered),'block':block,'block_indices':[index]})
        cursor = pos+len(rendered)
    if not found or text[cursor:].strip():
        return [{'start':0,'end':len(text),'block':{'type':'paragraph'},'block_indices':[],
                 'mapping_fallback':True}]
    found[0]['start']=0
    for a,b in zip(found,found[1:]): a['end']=b['start']
    found[-1]['end']=len(text)
    return found


def heading_contexts(rows):
    stack=[]; contexts=[]
    for i,row in enumerate(rows):
        b=row['block']
        # Statistic widgets use heading markup for the number and its label.
        numeric=b['type']=='heading' and bool(NUMBER_HEADING.fullmatch(b.get('text','')))
        follows_number=i>0 and rows[i-1]['block']['type']=='heading' and bool(NUMBER_HEADING.fullmatch(rows[i-1]['block'].get('text','')))
        if b['type']=='heading' and not numeric and not follows_number:
            level=b['level']
            stack=[x for x in stack if x[0]<level]
            stack.append((level,b['text']))
        contexts.append([x[1] for x in stack])
    return contexts


def event_header(text):
    """Repeat an original event title and short facts, not invented fields."""
    parts=[p.strip() for p in text.split('\n\n') if p.strip()]
    heading=next((p for p in parts if re.match(r'#{1,6}\s',p)), '')
    # CMU uses a paragraph anchor for the title.
    if not heading:
        heading=next((p for p in parts if EVENT_LINK.search(p)), '')
    short=[p for p in parts if p!=heading and len(p)<160 and not p.startswith('[Source]')][:4]
    return '\n'.join(([heading] if heading else [])+short)


def units_for_document(doc):
    rows=mapped_blocks(doc); text=doc['content_markdown']; paths=heading_contexts(rows)
    units=[]
    def add(lo,hi,kind,context=None,warning=None):
        a,b=rows[lo]['start'],rows[hi-1]['end']
        units.append({'start':a,'end':b,'kind':kind,'section_path':paths[lo],
            'context':context if context is not None else ' > '.join(paths[lo]),
            'block_indices':[j for r in rows[lo:hi] for j in r['block_indices']],
            'warnings':[warning] if warning else []})
    if rows[0].get('mapping_fallback'):
        add(0,len(rows),'paragraph',warning='block_mapping_failed_use_markdown')
        return units

    # Recognize the acquired flat listing layouts. Explicit group blocks remain authoritative.
    host=urlsplit(doc.get('source_url') or (doc.get('sources') or [{}])[0].get('source_url','')).hostname or ''
    flat_calendar=host in {'events.cmu.edu','community.pghcitypaper.com','www.pittsburghsymphony.org','www.thefrickpittsburgh.org'}
    event_ranges={}
    if flat_calendar:
        markers=[i for i,r in enumerate(rows) if r['block']['type'] in {'paragraph','heading'}
                 and EVENT_LINK.search(text[r['start']:r['end']])
                 and not re.search(r'\[(?:More|Learn More|Source|Get tickets)',text[r['start']:r['end']],re.I)]
        # Calendar event URLs can appear in navigation; only group when multiple anchors exist.
        if len(markers)>=3:
            starts=[]
            for k,index in enumerate(markers):
                start=index
                # These two layouts place date/time/venue BEFORE the event title.
                if host in {'events.cmu.edu','community.pghcitypaper.com'}:
                    floor=markers[k-1]+1 if k else 0
                    date_index=None
                    for j in range(index-1,max(floor,index-5)-1,-1):
                        v=text[rows[j]['start']:rows[j]['end']].strip()
                        if len(v)<120 and DATE.search(v):
                            date_index=j;break
                        if len(v)>180 or rows[j]['block']['type']=='heading':break
                    if date_index is not None:
                        start=date_index
                        if host=='events.cmu.edu':
                            for j in range(date_index-1,max(floor,date_index-2)-1,-1):
                                v=text[rows[j]['start']:rows[j]['end']].strip()
                                if rows[j]['block']['type']=='paragraph' and 0<len(v)<120 and not EVENT_LINK.search(v):start=j
                                else:break
                starts.append(start)
            for k,start in enumerate(starts):
                end=starts[k+1] if k+1<len(starts) else len(rows)
                # Do not swallow unrelated sections after the last card; date headings also delimit days.
                for j in range(markers[k]+1,end):
                    b=rows[j]['block']
                    if b['type']=='heading' and b['level']<=3:
                        end=j;break
                if end>start:event_ranges[start]=end

    i=0
    while i<len(rows):
        if i in event_ranges:
            end=event_ranges[i]
            add(i,end,'event',event_header(text[rows[i]['start']:rows[end-1]['end']]),'flat_event_boundary_heuristic')
            i=end;continue
        b=rows[i]['block'];kind=b['type']
        if kind=='group':
            s=text[rows[i]['start']:rows[i]['end']]
            is_event=bool(EVENT_LINK.search(s)) or bool(DATE.search(s))
            add(i,i+1,'event' if is_event else 'card',event_header(s) if is_event else ' > '.join(paths[i]))
        elif kind=='heading' and NUMBER_HEADING.fullmatch(b.get('text','')) and i+1<len(rows):
            add(i,i+2,'statistic');i+=2;continue
        elif kind=='table':
            header=b.get('caption','')
            if b.get('rows') and any(c.get('header') for c in b['rows'][0]):
                header+='\n'+' | '.join(c['text'] for c in b['rows'][0])
            complex_table=any(c.get('rowspan',1)>1 or c.get('colspan',1)>1 for row in b.get('rows',[]) for c in row)
            add(i,i+1,'table',' > '.join(paths[i])+'\n'+header,
                'merged_cells_preserved_but_not_reconstructed' if complex_table else None)
        elif kind=='pdf_layout':
            body=b.get('text','')
            lines=body.splitlines(keepends=True)
            tabular=sum(bool(re.search(r'\S {3,}\S',x) and re.search(r'\d',x)) for x in lines)>=3
            # Repeat only original lines before first data-looking numeric row.
            header=[]
            for line in lines:
                if re.search(r'\d[\d,]*\.\d|\d{1,3}(?:,\d{3})+',line):break
                if line.strip():header.append(line.strip())
                if len(header)>=8:break
            add(i,i+1,'pdf_layout_rows' if tabular else 'pdf_text','\n'.join(header),
                'pdf_layout_not_semantic_cells; no OCR or table repair')
            # Keep the source page heading with its PDF body instead of an orphan chunk.
            if len(units)>1 and units[-2]['kind']=='heading' and units[-2]['end']==units[-1]['start']:
                previous=units.pop(-2)
                units[-1]['start']=previous['start']
                units[-1]['block_indices']=previous['block_indices']+units[-1]['block_indices']
        else:add(i,i+1,kind)
        i+=1
    return units


def sentence_boundaries(text):
    """Conservative English sentence heuristic; preserve separators and abbreviations."""
    ends=[]
    for m in re.finditer(r'(?<=[.!?])\s+(?=[A-Z\d"“])',text):
        before=text[max(0,m.start()-128):m.start()]
        last=before.split()[-1].lower() if before.split() else ''
        if last in {'mr.','mrs.','ms.','dr.','prof.','st.','jr.','sr.','vs.','etc.','e.g.','i.e.','a.m.','p.m.','u.s.'}:continue
        if re.search(r'\b[A-Z]\.$',before):continue
        ends.append(m.end())
    return ends


def preferred_boundaries(text,kind):
    if kind=='table':
        # Our Markdown renderer writes one row per line, including HTML <tr>.
        return [m.end() for m in re.finditer(r'\n',text)]
    if kind in {'code','pdf_layout_rows'}:
        return [m.end() for m in re.finditer(r'\n',text)]
    if kind in {'list','facts'}:
        return [m.end() for m in re.finditer(r'\n(?=(?:- |\d+\. ))',text)]
    paragraphs=[m.end() for m in re.finditer(r'\n\s*\n',text)]
    return sorted(set(paragraphs+sentence_boundaries(text)))
