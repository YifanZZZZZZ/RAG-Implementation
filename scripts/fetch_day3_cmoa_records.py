"""Read-only access to the public search endpoint used by the CMOA collection site.
Fetches metadata only, not images. UI public search credentials are kept in memory.
"""
import argparse,base64,json,re,time
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.parse import urljoin

ROOT=Path(__file__).resolve().parents[1]
HOST='https://collection.carnegieart.org/'

def read(url):
    with urlopen(url,timeout=30) as r:return r.read()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--questions',type=Path,default=ROOT/'data/evaluation/test_set_day_3.json')
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.output.exists():raise FileExistsError('Use a new cache folder')
    html=read(HOST).decode()
    entry=re.search(r'<script[^>]+src="([^\"]*assets/index-[^\"]+\.js)"',html)
    if not entry:raise ValueError('Website entry bundle changed; inspect before continuing')
    js=read(urljoin(HOST,entry[1])).decode()
    home=re.search(r'\./(Home-[A-Za-z0-9]+\.js)',js)
    if not home:raise ValueError('Website search bundle changed')
    source=read(urljoin(HOST,'assets/'+home[1])).decode()
    match=re.search(r'"(collections:[^\"]+)"',source)
    if not match:raise ValueError('Public search configuration changed; inspect before continuing')
    auth=base64.b64encode(match[1].encode()).decode()
    headers={'Content-Type':'application/json','Authorization':'Basic '+auth}
    a.output.mkdir(parents=True)
    for q in json.loads(a.questions.read_text()):
        if 'Carnegie Museum of Art' not in q['question']:continue
        title=re.search('“(.+)”',q['question'])
        if not title:continue
        body={'size':100,'query':{'match_phrase':{'title':title[1]}}}
        req=Request(HOST+'api/cmoa_objects/object/_search',data=json.dumps(body).encode(),headers=headers)
        result=json.loads(read(req))
        if result.get('timed_out') or result.get('_shards',{}).get('failed'):
            raise RuntimeError('Incomplete source search')
        record={'question':q,'searched_title':title[1],'response':result,
                'retrieved_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
        (a.output/(str(q['id'])+'.json')).write_text(json.dumps(record,ensure_ascii=False)+'\n')
        print(q['id'],title[1],'hits',result['hits']['total'],flush=True)
        time.sleep(.2)

if __name__=='__main__':main()
