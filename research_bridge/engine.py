from __future__ import annotations
import asyncio
import hashlib
import html
import json
import os
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse, urlencode

import httpx
from bs4 import BeautifulSoup

PLATFORMS = {
    'taobao': 'taobao.com', 'jd': 'jd.com', 'pdd': 'pinduoduo.com',
    'xianyu': 'goofish.com', 'smzdm': 'smzdm.com', 'amazon': 'amazon.com',
    'zhihu': 'zhihu.com', 'bilibili': 'bilibili.com', 'douyin': 'douyin.com',
    'xiaohongshu': 'xiaohongshu.com', 'tiktok': 'tiktok.com',
    'youtube': 'youtube.com', 'tieba': 'tieba.baidu.com', 'reddit': 'reddit.com',
}
SHOPPING = ('taobao','jd','pdd','xianyu','smzdm','amazon')
REVIEWS = ('zhihu','bilibili','douyin','xiaohongshu','tiktok','youtube','tieba','reddit','smzdm')
ALLOWED_DOMAINS = set(PLATFORMS.values()) | {'item.taobao.com','detail.tmall.com','tmall.com','3.cn','item.jd.com','www.amazon.co.jp','amazon.co.jp','www.amazon.com','www.goofish.com','www.bing.com'}
REQ_TIMEOUT = httpx.Timeout(14, connect=5)
UTC8 = timezone(timedelta(hours=8))
MAX_RESULTS = 12


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def norm_platforms(platforms, default):
    if not platforms:
        return list(default)
    if isinstance(platforms, str):
        platforms = [p.strip() for p in platforms.split(',') if p.strip()]
    names = [str(v).lower().strip() for v in platforms]
    unknown = sorted(set(names) - set(PLATFORMS))
    if unknown:
        raise ValueError('Unsupported platforms: ' + ', '.join(unknown))
    return list(dict.fromkeys(names))


def clean(v):
    return re.sub(r'\s+', ' ', html.unescape(str(v or ''))).strip()


def decimal_value(x):
    if x is None or isinstance(x,bool):return None
    raw = re.sub(r'[¥￥$,，\s]', '',str(x))
    try:
        val=Decimal(raw)
        if 0 < val <= 10_000_000:
            return float(val)
    except (InvalidOperation,ValueError):pass
    return None


def recursive_find(obj, keys, max_depth=9):
    if max_depth <= 0:return None
    if isinstance(obj,dict):
        for k in keys:
            if k in obj and obj[k] not in ('',None): return obj[k]
        for val in obj.values():
            r=recursive_find(val,keys,max_depth-1)
            if r is not None:return r
    if isinstance(obj,list):
        for x in obj[:100]:
            r=recursive_find(x,keys,max_depth-1)
            if r is not None:return r
    return None


def locate_list(obj, keys, max_depth=8):
    if max_depth<0:return []
    if isinstance(obj,dict):
        for k in keys:
            if k in obj:
                v=obj[k]
                if isinstance(v,list):return v
                if isinstance(v,str):
                    try:
                        p=json.loads(v)
                        r=locate_list(p,keys,max_depth-1)
                        if r:return r
                    except ValueError: pass
                if isinstance(v,dict):
                    r=locate_list(v,keys,max_depth-1)
                    if r:return r
        for v in obj.values():
            r=locate_list(v,keys,max_depth-1)
            if r:return r
    return []


def check_site_url(url):
    parsed=urlparse(str(url))
    host=(parsed.hostname or '').lower()
    if parsed.scheme!='https' or parsed.port not in (None,443):
        raise ValueError('Only https public site URLs are allowed')
    if host not in ALLOWED_DOMAINS and not any(host.endswith('.'+d) for d in ALLOWED_DOMAINS):
        raise ValueError('URL is outside the allowed sites')
    if parsed.username or parsed.password:raise ValueError('Embedded credentials not allowed')
    return url


class ResearchEngine:
    def __init__(self, db_path=None, client=None):
        self.db_path=str(db_path or os.environ.get('RESEARCH_DB','./data/observations.sqlite3'))
        Path(self.db_path).parent.mkdir(parents=True,exist_ok=True)
        self.client=client
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as con:
            con.execute('CREATE TABLE IF NOT EXISTS observations (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, platform TEXT NOT NULL, query TEXT NOT NULL, title TEXT, url TEXT, price REAL, currency TEXT, price_kind TEXT NOT NULL, provenance TEXT NOT NULL)')
            con.execute('CREATE INDEX IF NOT EXISTS idx_platform_query ON observations(platform,query)')

    def _record(self, row, query):
        try:
            with sqlite3.connect(self.db_path) as con:
                con.execute('INSERT INTO observations(created_at,platform,query,title,url,price,currency,price_kind,provenance) VALUES (?,?,?,?,?,?,?,?,?)',
                (now_iso(),row.get('platform',''),query,row.get('title'),row.get('url'),row.get('price'),row.get('currency'),row.get('price_kind','unknown'),row.get('provenance','')))
        except (OSError,sqlite3.Error): pass

    async def _get(self,url,headers=None,params=None):
        if self.client:return await self.client.get(url,headers=headers,params=params,timeout=REQ_TIMEOUT,follow_redirects=False)
        async with httpx.AsyncClient(timeout=REQ_TIMEOUT,follow_redirects=False) as client:
            return await client.get(url,headers=headers,params=params)

    async def _post(self,url,data=None):
        if self.client:return await self.client.post(url,data=data,timeout=REQ_TIMEOUT)
        async with httpx.AsyncClient(timeout=REQ_TIMEOUT) as client:
            return await client.post(url,data=data)

    def source_status(self):
        configured=[]
        if os.environ.get('BRAVE_SEARCH_API_KEY'):configured.append('brave_search')
        if all(os.environ.get(k) for k in ('JD_APP_KEY','JD_APP_SECRET')):configured.append('jd_union')
        if all(os.environ.get(k) for k in ('TAOBAO_APP_KEY','TAOBAO_APP_SECRET','TAOBAO_ADZONE_ID')):configured.append('taobao_union')
        if os.environ.get('TINYFISH_API_KEY'):configured.append('tinyfish_agent_api')
        return {'platforms':list(PLATFORMS), 'configured':configured,
          'fallback':'bing_rss_public_index (best-effort; no guaranteed indexed products)',
          'note':'No consumer login cookies are used. An API is configured only if its credentials exist. Never infer checkout prices.',
          'timestamp':now_iso()}

    async def _search(self,q,limit):
        if not q.strip():raise ValueError('Empty query')
        limit=max(1,min(MAX_RESULTS,int(limit)))
        key=os.environ.get('BRAVE_SEARCH_API_KEY')
        if key:
            try:
                response=await self._get('https://api.search.brave.com/res/v1/web/search',
                    headers={'Accept':'application/json','X-Subscription-Token':key},params={'q':q,'count':limit})
                response.raise_for_status()
                out=[]
                for rec in response.json().get('web',{}).get('results',[])[:limit]:
                    u=rec.get('url','')
                    if u.startswith('https://'):
                        out.append({'title':clean(rec.get('title')),'url':u,'snippet':clean(rec.get('description')),'index':'brave'})
                return out, None
            except (httpx.HTTPError,ValueError,KeyError) as exc:
                error=f'Brave Search: {type(exc).__name__}'
            else:error=None
        else:error=None
        try:
            response=await self._get('https://www.bing.com/search',params={'q':q,'format':'rss'},headers={'User-Agent':'Mozilla/5.0 ResearchBridge/1.0'})
            response.raise_for_status()
            root=ET.fromstring(response.text)
            out=[]
            for item in root.findall('.//item')[:limit]:
                title=clean(item.findtext('title'))
                url=clean(item.findtext('link'))
                description=clean(BeautifulSoup(item.findtext('description') or '', 'html.parser').get_text(' ',strip=True))
                if url.startswith('https://'):
                    out.append({'title':title,'url':url,'snippet':description,'index':'bing_rss'})
            return out,error
        except (httpx.HTTPError,ET.ParseError,ValueError) as exc:
            return [], '; '.join(filter(None,[error,f'Bing RSS: {type(exc).__name__}']))

    async def _by_platform(self,query,platform,limit, suffix=''):
        domain=PLATFORMS[platform]
        full=f'site:{domain} {query} {suffix}'.strip()
        hits,error=await self._search(full,limit)
        good=[]
        for h in hits:
            host=urlparse(h['url']).hostname or ''
            if host==domain or host.endswith('.'+domain):
                good.append({'platform':platform,**h,'price':None,'currency':None,
                    'price_kind':'not_verified','provenance':'search_index','observed_at':now_iso()})
        return {'platform':platform,'items':good,'search_error':error,'query':full}

    async def search_products(self,query,platforms=None,limit=5):
        platforms=norm_platforms(platforms,SHOPPING)
        limit=max(1,min(MAX_RESULTS,int(limit)))
        tasks=[self._by_platform(query,p,limit,'价格 商品') for p in platforms]
        scanned=await asyncio.gather(*tasks,return_exceptions=True)
        out=[];errors=[]
        for p,x in zip(platforms,scanned):
            if isinstance(x,Exception):errors.append({'platform':p,'message':str(x)[:180]})
            else:
                out+=x['items']
                if x['search_error']:errors.append({'platform':p,'message':x['search_error']})
        for x in out:self._record(x,query)
        return {'query':query,'results':out,'count':len(out),'errors':errors,
                'warning':'Search-index results are leads, NOT verified product prices. API quotes, if available, come from get_product_prices.',
                'timestamp':now_iso()}

    async def search_reviews(self,query,platforms=None,limit=4):
        platforms=norm_platforms(platforms,REVIEWS)
        limit=max(1,min(MAX_RESULTS,int(limit)))
        tasks=[self._by_platform(query,p,limit,'测评 使用体验 优缺点') for p in platforms]
        scanned=await asyncio.gather(*tasks,return_exceptions=True)
        out=[];errors=[]
        for p,x in zip(platforms,scanned):
            if isinstance(x,Exception):errors.append({'platform':p,'message':str(x)[:180]})
            else:
                out+=x['items']
                if x['search_error']:errors.append({'platform':p,'message':x['search_error']})
        return {'query':query,'results':out,'count':len(out),'errors':errors,
                'warning':'Search snippets are pointers, not independently verified review conclusions.',
                'timestamp':now_iso()}

    async def _jd_union(self,query,limit):
        key=os.environ.get('JD_APP_KEY');secret=os.environ.get('JD_APP_SECRET')
        if not key or not secret:return [],'not_configured'
        ts=datetime.now(UTC8).strftime('%Y-%m-%d %H:%M:%S')
        payload=json.dumps({os.environ.get('JD_UNION_REQUEST_FIELD','goodsReqDTO'):{'keyword':query,'pageSize':min(limit,20),'pageIndex':1}},ensure_ascii=False,separators=(',',':'))
        p={'method':'jd.union.open.goods.query','app_key':key,'timestamp':ts,'format':'json','v':'1.0','sign_method':'md5','360buy_param_json':payload}
        raw=secret+''.join(k+str(p[k]) for k in sorted(p))+secret
        p['sign']=hashlib.md5(raw.encode('utf-8')).hexdigest().upper()
        try:
            r=await self._post('https://api.jd.com/routerjson',data=p);r.raise_for_status()
            obj=r.json()
            if 'error_response' in obj:return [],'API error: '+str(obj['error_response'])[:250]
            nested=recursive_find(obj,('queryResult','result'))
            if isinstance(nested,str):
                try:obj=json.loads(nested)
                except ValueError:pass
            elif isinstance(nested,dict):obj=nested
            goods=locate_list(obj,('data','goodsList','goods'))
            offers=[]
            for a in goods[:limit]:
                if not isinstance(a,dict):continue
                sku=a.get('skuId') or a.get('sku_id')
                details=a.get('priceInfo') or {}
                price=decimal_value(details.get('price') if isinstance(details,dict) else None)
                if price is None: price=decimal_value(a.get('price'))
                if price is None:continue
                offers.append({'platform':'jd','title':clean(a.get('skuName') or a.get('goodsName')),
                  'sku':str(sku or ''),'url':('https://item.jd.com/'+str(sku)+'.html') if sku else None,
                  'price':price,'currency':'CNY','price_kind':'official_union_quote',
                  'checkout_verified':False,'provenance':'jd.union.open.goods.query','observed_at':now_iso()})
            return offers,None
        except (httpx.HTTPError,ValueError,KeyError,TypeError) as exc:
            return [],type(exc).__name__+': '+str(exc)[:180]

    async def _taobao_union(self,query,limit):
        key=os.environ.get('TAOBAO_APP_KEY');secret=os.environ.get('TAOBAO_APP_SECRET');zone=os.environ.get('TAOBAO_ADZONE_ID')
        if not (key and secret and zone):return [],'not_configured'
        ts=datetime.now(UTC8).strftime('%Y-%m-%d %H:%M:%S')
        p={'method':'taobao.tbk.dg.material.optional','app_key':key,'timestamp':ts,'v':'2.0','format':'json','sign_method':'md5',
            'adzone_id':zone,'q':query,'page_size':str(min(limit,20)),'page_no':'1'}
        raw=secret+''.join(k+str(p[k]) for k in sorted(p))+secret
        p['sign']=hashlib.md5(raw.encode('utf-8')).hexdigest().upper()
        try:
            r=await self._post('https://eco.taobao.com/router/rest',data=p);r.raise_for_status()
            obj=r.json()
            if 'error_response' in obj:return [],'API error: '+str(obj['error_response'])[:250]
            goods=locate_list(obj,('map_data','data','items'))
            offers=[]
            for a in goods[:limit]:
                if not isinstance(a,dict):continue
                price=decimal_value(a.get('zk_final_price') or a.get('final_promotion_price') or a.get('reserve_price'))
                if price is None:continue
                url=a.get('item_url') or a.get('click_url')
                sku=a.get('num_iid') or a.get('item_id')
                if not url and sku:url='https://item.taobao.com/item.htm?id='+str(sku)
                offers.append({'platform':'taobao','title':clean(a.get('title') or a.get('item_title')),
                  'sku':str(sku or ''),'url':url,'price':price,'currency':'CNY',
                  'price_kind':'official_union_quote','checkout_verified':False,
                  'provenance':'taobao.tbk.dg.material.optional','observed_at':now_iso()})
            return offers,None
        except (httpx.HTTPError,ValueError,KeyError,TypeError) as exc:
            return [],type(exc).__name__+': '+str(exc)[:180]

    async def get_product_prices(self,query,platforms=None,limit=5):
        platforms=norm_platforms(platforms,SHOPPING)
        limit=max(1,min(MAX_RESULTS,int(limit)))
        offers=[]; errors=[]; discovery=[]
        for platform in platforms:
            if platform=='jd':o,err=await self._jd_union(query,limit)
            elif platform=='taobao':o,err=await self._taobao_union(query,limit)
            else:o,err=[], 'no_official_price_adapter'
            offers.extend(o)
            if err:errors.append({'platform':platform,'status':err})
            if not o:
                result=await self._by_platform(query,platform,limit,'报价')
                discovery.extend(result['items'])
                if result['search_error']:
                    errors.append({'platform':platform,'status':result['search_error']})
        for x in offers:self._record(x,query)
        for x in discovery:self._record(x,query)
        return {'query':query,'quotes':offers,'discovery_links':discovery,'errors':errors,
                'quote_count':len(offers), 'price_guarantee':'Official union API quotes are NOT confirmed personal checkout prices. Search leads contain no verified price.',
                'timestamp':now_iso()}

    async def get_product_details(self,url):
        check_site_url(url)
        try:
            r=await self._get(url,headers={'Accept':'text/html,application/xhtml+xml','User-Agent':'Mozilla/5.0 (compatible; ProductResearch/1.0)'})
            if r.status_code in (301,302,303,307,308):
                return {'url':url,'status':'redirect','location':r.headers.get('location'), 'error':'Redirect not followed; check destination manually'}
            r.raise_for_status()
            if len(r.content)>2_000_000:return {'url':url,'status':'skipped','error':'Page too large'}
            if 'html' not in r.headers.get('content-type','').lower():return {'url':url,'status':'skipped','error':'Not an HTML page'}
            soup=BeautifulSoup(r.text,'html.parser')
            title=clean(soup.title.get_text(' ',strip=True) if soup.title else '')
            objects=[]
            for el in soup.select('script[type="application/ld+json"]')[:20]:
                try:
                    data=json.loads(el.string or el.get_text())
                    if isinstance(data,list):objects.extend(data[:15])
                    else:objects.append(data)
                except (ValueError,TypeError):pass
            price=recursive_find(objects,('price','lowPrice'))
            ccy=recursive_find(objects,('priceCurrency',))
            price=decimal_value(price)
            return {'url':url,'status':'ok','title':title,'jsonld':objects[:8],
              'price':price,'currency':ccy if price is not None else None,
              'price_kind':'page_structured_metadata' if price is not None else 'not_verified',
              'checkout_verified':False,'observed_at':now_iso(),
              'warning':'Structured page price may be stale or for a different SKU. Verify variant and checkout.'}
        except (httpx.HTTPError,ValueError,TypeError) as exc:
            return {'url':url,'status':'unavailable','error':type(exc).__name__+': '+str(exc)[:180]}


    async def browser_price_probe(self, url, query=''):
        """Optional independent TinyFish SDK access. Plugin OAuth is NOT an SDK API key."""
        check_site_url(url)
        key=os.environ.get('TINYFISH_API_KEY')
        if not key:
            return {'status':'not_configured', 'note':'Connected ChatGPT TinyFish plugin is separate from an API key; call TinyFish directly in ChatGPT Work instead.'}
        try:
            from tinyfish import AsyncTinyFish
        except ImportError:
            return {'status':'missing_dependency','note':'pip install tinyfish'}
        schema={'type':'object','properties':{'products':{'type':'array','items':{'type':'object','properties':{
           'title':{'type':'string'},'displayed_price':{'type':'string'},'currency':{'type':'string'},
           'variant':{'type':'string'},'seller':{'type':'string'},'url':{'type':'string'},
           'availability':{'type':'string'}},'required':['title','displayed_price','url']}}},'required':['products']}
        goal=('只读检索此网站当前页面上公开可见、与以下关键词对应的商品与标价：'+query+
              '。只收集确实在网页上看到的商品名、SKU/版本、价格及商品URL，不猜测价格、不登录、不尝试验证码、'+
              '不加入购物车或下单。遇到网站限制直接结束，原样保留货币单位。最多返回5个结果。')
        try:
            client=AsyncTinyFish(api_key=key, max_retries=0)
            response=await client.agent.run(url=url,goal=goal,output_schema=schema)
            result=response.result or {}
            if not isinstance(result,dict):result={}
            out=[]
            for obj in result.get('products',[])[:5]:
                if not isinstance(obj,dict):continue
                out.append({'title':clean(obj.get('title')),'displayed_price':clean(obj.get('displayed_price')),
                   'currency':clean(obj.get('currency')),'variant':clean(obj.get('variant')),
                   'seller':clean(obj.get('seller')),'url':obj.get('url'),
                   'price_kind':'browser_displayed_unverified_checkout','checkout_verified':False,
                   'observed_at':now_iso(),'provenance':'tinyfish_agent'})
            return {'status':str(response.status),'results':out,'source_url':url,
               'warning':'Browser page prices are not verified checkout prices and may be login/region dependent.'}
        except Exception as exc:
            return {'status':'unavailable','error':type(exc).__name__,
               'note':'Request failed; no automatic account or proxy rotation.'}

    def compare_offers(self,offers):
        if not isinstance(offers,list) or len(offers)>100:raise ValueError('offers must be a list of <=100 offers')
        verified=[];unverified=[]
        for a in offers:
            if not isinstance(a,dict):continue
            p=decimal_value(a.get('price'))
            label=str(a.get('price_kind',''))
            if p is not None and label in ('official_union_quote','page_structured_metadata','verified_checkout'):
                verified.append({**a,'price':p})
            else:unverified.append(a)
        verified.sort(key=lambda x:(x.get('currency','CNY'),x['price']))
        return {'price_eligible_offers':verified,'excluded_unverified':unverified,
          'warning':'Only compare identical variants and currencies. Official union quote is not verified checkout price.',
          'timestamp':now_iso()}

    def price_history(self,query,platform=None,limit=50):
        limit=max(1,min(250,int(limit)))
        params=[f'%{query}%',limit]
        cond='query LIKE ?'
        if platform:
            if platform not in PLATFORMS:raise ValueError('unknown platform')
            cond+=' AND platform=?';params=[f'%{query}%',platform,limit]
        with sqlite3.connect(self.db_path) as con:
            con.row_factory=sqlite3.Row
            rows=[dict(r) for r in con.execute('SELECT created_at,platform,query,title,url,price,currency,price_kind,provenance FROM observations WHERE '+cond+' ORDER BY id DESC LIMIT ?',params)]
        return {'results':rows,'count':len(rows),'warning':'Historical observations may include unverified leads. They are not proven transaction prices.'}