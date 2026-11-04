"""First-pass JSON-LD event discovery. Not yet a complete cinema feed."""
import json,re
from datetime import datetime,timezone
from pathlib import Path
import requests
from bs4 import BeautifulSoup
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'data'/'sessions.json'
SOURCES=[('Dendy Canberra','https://www.dendy.com.au/'),('Palace Electric','https://www.palacecinemas.com.au/'),('NFSA Arc Cinema','https://www.nfsa.gov.au/')]
def events(obj):
 if isinstance(obj,dict):
  typ=obj.get('@type',[]);typ=[typ] if isinstance(typ,str) else typ
  if any('Event' in x for x in typ):yield obj
  for v in obj.values():yield from events(v)
 elif isinstance(obj,list):
  for v in obj:yield from events(v)
def main():
 rows=[];status=[];cutoff=datetime.now(timezone.utc).year-10
 for cinema,url in SOURCES:
  try:
   r=requests.get(url,headers={'User-Agent':'CanberraRetroCinema/0.1'},timeout=25);r.raise_for_status();soup=BeautifulSoup(r.text,'html.parser');found=[]
   for tag in soup.select('script[type="application/ld+json"]'):
    try:data=json.loads(tag.string or tag.get_text())
    except Exception:continue
    for e in events(data):
     name=str(e.get('name','')).strip();start=e.get('startDate')
     if not name or not start:continue
     about=e.get('about',{});year=about.get('datePublished','') if isinstance(about,dict) else ''
     if not year and isinstance(e.get('workPerformed'),dict):year=e['workPerformed'].get('datePublished','')
     try:year=int(str(year)[:4])
     except (ValueError,TypeError):continue
     if year>cutoff:continue
     found.append({'film_id':re.sub(r'[^a-z0-9]+','-',name.lower()).strip('-'),'title':name,'year':year,'cinema':cinema,'start':start,'description':e.get('description',''),'ticket_url':e.get('url',''),'poster_url':''})
   rows.extend(found);status.append({'cinema':cinema,'status':'ok' if found else 'no supported events found','count':len(found)})
  except Exception as ex:status.append({'cinema':cinema,'status':'fetch error','detail':str(ex)[:200]});print('WARN',cinema,ex)
 unique={(x['film_id'],x['cinema'],x['start']):x for x in rows}
 OUT.write_text(json.dumps({'updated_at':datetime.now(timezone.utc).isoformat(),'sessions':sorted(unique.values(),key=lambda x:x['start']),'sources':status},indent=2)+'\\n')
 print('Candidate sessions:',len(unique))
if __name__=='__main__':main()
