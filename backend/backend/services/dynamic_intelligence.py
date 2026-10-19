"""Fixed official lookup catalogue, persisted through the existing revision pipeline.

No caller supplies a URL. Failures are failures, not negative vulnerability facts.
The process cache is bounded and carries its actual fetch time; Redis also limits
concurrent workers. The ordinary collection scheduler is not enabled or changed.
"""
from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import threading
import time
from ipaddress import ip_address
from urllib.parse import urlsplit, urlunsplit

import httpx
from sqlalchemy import select

from backend.collectors.base import CollectionBatch
from backend.collectors.extended import NVD_URL, nvd_record
from backend.collectors.live import OSV_URL, parse_osv, plain, timestamp
from backend.core.config import get_settings
from backend.models import DataSource, Document, TaskRun
from backend.schemas.contracts import RawRecord
from backend.services.jobs import utc_now
from backend.services.pipeline import ingest_batch

CISA_URL = 'https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json'
CISA_MIRROR = 'https://raw.githubusercontent.com/cisagov/kev-data/develop/known_exploited_vulnerabilities.json'
OWASP = {
    'agency': 'https://genai.owasp.org/llmrisk/llm062025-excessive-agency/',
    'injection': 'https://genai.owasp.org/llmrisk/llm01-prompt-injection/',
    'poisoning': 'https://genai.owasp.org/llmrisk/llm042025-data-and-model-poisoning/',
}
_CACHE = OrderedDict()
_LOCK = threading.Lock()
_TTL = {'nvd_lookup': 900, 'kev_lookup': 900, 'osv_lookup': 900, 'knowledge_lookup': 3600, 'web_search': 300}
_LAST = {}
TAVILY_URL = 'https://api.tavily.com/search'
PREFERRED_SEARCH_DOMAINS = ('nvd.nist.gov','cisa.gov','owasp.org','nist.gov','mitre.org')
_SEARCH_SLOTS = threading.BoundedSemaphore(2)
_DNS_SLOTS = threading.BoundedSemaphore(2)
_DNS_POOL = ThreadPoolExecutor(max_workers=2,thread_name_prefix='search-url-dns')


class IntelligenceError(RuntimeError):
    pass


def _search_key(settings):
    """Read a backend-only key, never retain it in a result or public error."""
    secret = settings.tavily_api_key
    path = settings.tavily_api_key_file
    if secret and secret.get_secret_value().strip() and path:
        raise IntelligenceError('search_not_configured')
    value = secret.get_secret_value().strip() if secret else ''
    if not value and path:
        try:
            descriptor = os.open(Path(path), os.O_RDONLY | os.O_NONBLOCK | getattr(os,'O_NOFOLLOW',0))
            with os.fdopen(descriptor,'rb') as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or not 0 < info.st_size <= 4096:
                    raise ValueError()
                value = handle.read(4097).decode('utf-8').strip()
        except (OSError,UnicodeError,ValueError):
            raise IntelligenceError('search_not_configured') from None
    if not value or len(value)>4096 or any(ord(c)<33 or ord(c)>126 for c in value):
        raise IntelligenceError('search_not_configured')
    return value


def _public_search_url(value, *, deadline=None):
    """Validate result provenance; no result URL is fetched by this service.

    All DNS answers must be public. Disabling redirects on the fixed provider
    request and using vendor raw_content avoids an arbitrary-URL HTTP stack.
    """
    if not isinstance(value,str) or len(value)>2048 or any(ord(c)<33 for c in value) or '\\' in value:
        raise IntelligenceError('search_unsafe_url')
    try:
        parsed=urlsplit(value)
        port=parsed.port
        host=(parsed.hostname or '').rstrip('.').encode('idna').decode('ascii').lower()
    except (ValueError,UnicodeError):
        raise IntelligenceError('search_unsafe_url') from None
    if (parsed.scheme!='https' or not host or parsed.username or parsed.password or port not in {None,443}
            or host in {'localhost','metadata','host.docker.internal'}
            or host.endswith(('.localhost','.local','.internal','.invalid'))):
        raise IntelligenceError('search_unsafe_url')
    try:literal=ip_address(host)
    except ValueError:literal=None
    def unsafe(address):
        return not address.is_global or address.is_multicast or address.is_reserved or address.is_unspecified
    if literal is not None and unsafe(literal):raise IntelligenceError('search_unsafe_url')
    if not _DNS_SLOTS.acquire(blocking=False):raise IntelligenceError('search_unsafe_url')
    def resolve():
        try:return socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
        finally:_DNS_SLOTS.release()
    try:
        remaining=min(1.0,deadline-time.monotonic()) if deadline is not None else 1.0
        if remaining<=0:
            _DNS_SLOTS.release()
            raise IntelligenceError('search_timeout')
        future=_DNS_POOL.submit(resolve)
        answers=future.result(timeout=remaining)
        addresses={row[4][0] for row in answers}
        if not addresses or any(unsafe(ip_address(address)) for address in addresses):
            raise ValueError()
    except (OSError,ValueError,FutureTimeout):
        raise IntelligenceError('search_unsafe_url') from None
    canonical_host='['+host+']' if ':' in host else host
    return urlunsplit((parsed.scheme,canonical_host,parsed.path,parsed.query,'')),host


def _tavily_request(key, query, settings, deadline):
    body={'query':query,'topic':'general','search_depth':'basic','max_results':settings.tavily_max_results,
        'include_answer':False,'include_raw_content':'markdown','include_published_date':True,
        'include_images':False,'include_favicon':False,'auto_parameters':False,'include_usage':True,
        'include_domains':list(PREFERRED_SEARCH_DOMAINS),'include_domains_mode':'prefer'}
    for attempt in range(2):
        remaining=deadline-time.monotonic()
        if remaining<=0:raise IntelligenceError('search_timeout')
        try:
            with httpx.Client(timeout=remaining,follow_redirects=False,trust_env=False) as client:
                with client.stream('POST',TAVILY_URL,headers={'Authorization':'Bearer '+key},json=body) as response:
                    status=response.status_code
                    if status in {401,403}:raise IntelligenceError('search_auth_failed')
                    if status in {429,432,433}:raise IntelligenceError('search_rate_limited')
                    if status in {500,502,503,504}:raise IntelligenceError('search_source_unavailable')
                    if not 200<=status<300:raise IntelligenceError('search_invalid_response')
                    raw=bytearray()
                    for part in response.iter_bytes():
                        if time.monotonic()>=deadline:raise IntelligenceError('search_timeout')
                        raw.extend(part)
                        if len(raw)>1_000_000:raise IntelligenceError('search_response_too_large')
                    if time.monotonic()>=deadline:raise IntelligenceError('search_timeout')
                    if key.encode() in raw:raise IntelligenceError('search_invalid_response')
                    try:payload=_object(raw)
                    except IntelligenceError:raise IntelligenceError('search_invalid_response') from None
                    if key in json.dumps(payload,ensure_ascii=False):raise IntelligenceError('search_invalid_response')
                    return payload
        except httpx.TimeoutException:
            error='search_timeout'
        except httpx.HTTPError:
            error='search_source_unavailable'
        except IntelligenceError as exc:
            error=str(exc)
            if error not in {'search_source_unavailable','search_timeout'}:raise
        if attempt==1:raise IntelligenceError(error) from None
        if deadline-time.monotonic()<=0.1:raise IntelligenceError('search_timeout')
        time.sleep(0.1)
    raise IntelligenceError('search_source_unavailable')


def _web_search(query):
    settings=get_settings()
    secret=_search_key(settings)
    if not isinstance(query,str) or not query.strip() or len(query)>2000 or secret in query:
        raise IntelligenceError('search_invalid_response')
    query=query.strip()
    key=('web_search',query)
    with _LOCK:
        cached=_CACHE.get(key)
        if cached and time.monotonic()-cached[0]<settings.tavily_cache_seconds:
            return cached[1],cached[2],True
    if not _SEARCH_SLOTS.acquire(blocking=False):raise IntelligenceError('search_busy')
    try:
        deadline=time.monotonic()+settings.tavily_timeout_seconds
        try:_limit('web_search')
        except IntelligenceError:raise IntelligenceError('search_rate_limited') from None
        payload=_tavily_request(secret,query,settings,deadline)
        rows=payload.get('results')
        if not isinstance(rows,list) or len(rows)>20:raise IntelligenceError('search_invalid_response')
        checked=utc_now().isoformat()
        records=[];seen=set();discarded=False
        for item in rows[:settings.tavily_max_results]:
            if not isinstance(item,dict):raise IntelligenceError('search_invalid_response')
            try:url,host=_public_search_url(item.get('url'),deadline=deadline)
            except IntelligenceError as error:
                if str(error)=='search_timeout':raise
                discarded=True;continue
            if url in seen:continue
            seen.add(url)
            title=item.get('title');snippet=item.get('content');raw=item.get('raw_content')
            if not isinstance(title,str) or not title.strip() or not isinstance(snippet,str) or raw is not None and not isinstance(raw,str):
                raise IntelligenceError('search_invalid_response')
            title=plain(title)
            if not title:raise IntelligenceError('search_invalid_response')
            has_raw=isinstance(raw,str) and bool(raw.strip())
            content=plain(raw if has_raw else snippet)
            if len(content)<40:continue
            snippet=plain(snippet)[:4000]
            published=item.get('published_date')
            # Tavily dates are provider estimates; do not make them exact source
            # publication times or reinterpret them as official update times.
            metadata={'search_provider':'tavily','retrieval_kind':'web_search','search_query':query,
                'checked_at':checked,'original_url':item['url'],'source_host':host,
                'retrieval_snippet':snippet,'content_origin':'provider_raw_content' if has_raw else 'provider_search_snippet',
                'independent_fetch':False,'full_text_verified':False,'content_truncated':len(content)>12000,
                'source_trust':'preferred_official_domain' if any(host==x or host.endswith('.'+x) for x in PREFERRED_SEARCH_DOMAINS) else 'unreviewed_web_source'}
            if isinstance(published,str) and len(published)<=100:metadata['provider_date_estimate']=published
            usage=payload.get('usage',{})
            if isinstance(usage,dict) and type(usage.get('credits')) in {int,float} and 0<=usage['credits']<=1000:
                metadata['search_usage']={'credits':usage['credits']}
            # Only known OWASP/NIST pages are standards, never arbitrary blogs.
            standard=any(host==x or host.endswith('.'+x) for x in ('owasp.org','nist.gov'))
            records.append(RawRecord(external_id='tavily:'+hashlib.sha256(url.encode()).hexdigest(),title=title[:500],source_url=url,
                document_type='standard' if standard else 'article',content=content[:12000],is_demo=False,
                raw={'provider':'tavily','result':{key:item.get(key) for key in ('title','url','content','raw_content','published_date')}},
                metadata_json=metadata,content_completeness='partial' if has_raw else 'summary'))
            if time.monotonic()>=deadline:raise IntelligenceError('search_timeout')
        if discarded and not records:raise IntelligenceError('search_unsafe_url')
        value={'records':records,'note':'web_search_unsafe_results_discarded' if discarded else
            'web_search_provider_content_not_independently_fetched' if records else 'web_search_no_results'}
        with _LOCK:
            _CACHE[key]=(time.monotonic(),value,checked)
            while len(_CACHE)>128:_CACHE.popitem(last=False)
        return value,checked,False
    finally:_SEARCH_SLOTS.release()


def _json_request(method, url, *, params=None, body=None, headers=None, timeout=8, deadline=None):
    # A fixed origin/path was chosen in fetch(); do not follow redirects.
    deadline = deadline if deadline is not None else time.monotonic() + timeout
    # Only one retry for transient service/transport failures. 429 is returned
    # immediately: retrying it without its instructed delay harms the source.
    for attempt in range(2):
        remaining = min(timeout, deadline - time.monotonic())
        if remaining <= 0:
            raise IntelligenceError('intelligence_timeout')
        try:
            with httpx.Client(timeout=remaining, follow_redirects=False, trust_env=False) as client:
                with client.stream(method, url, params=params, json=body, headers=headers) as response:
                    if response.status_code == 429:
                        raise IntelligenceError('intelligence_rate_limited')
                    if response.status_code == 404:
                        return None
                    if response.status_code in {502, 503, 504}:
                        raise IntelligenceError('intelligence_source_unavailable')
                    if not 200 <= response.status_code < 300:
                        # Other statuses (including redirect and 403) are not
                        # transient. Never follow an unregistered destination.
                        raise IntelligenceError('intelligence_source_rejected')
                    raw = bytearray()
                    for part in response.iter_bytes():
                        if time.monotonic() >= deadline:
                            raise IntelligenceError('intelligence_timeout')
                        raw.extend(part)
                        if len(raw) > 3_000_000:
                            raise IntelligenceError('intelligence_response_too_large')
                    if time.monotonic() >= deadline:
                        raise IntelligenceError('intelligence_timeout')
                    if not raw.strip():
                        raise IntelligenceError('intelligence_invalid_response')
                    return bytes(raw)
        except httpx.TimeoutException:
            failure = 'intelligence_timeout'
        except httpx.HTTPError:
            failure = 'intelligence_source_unavailable'
        except IntelligenceError as error:
            failure = str(error)
            if failure not in {'intelligence_source_unavailable', 'intelligence_timeout'}:
                if failure == 'intelligence_source_rejected':
                    failure = 'intelligence_source_unavailable'
                raise IntelligenceError(failure) from None
        if time.monotonic() >= deadline:
            raise IntelligenceError('intelligence_timeout')
        if attempt == 1:
            raise IntelligenceError(failure) from None
        remaining = deadline - time.monotonic()
        if remaining <= 0.1:
            raise IntelligenceError('intelligence_timeout')
        time.sleep(0.1)
    raise IntelligenceError('intelligence_source_unavailable')


def _object(raw):
    """Valid JSON object only; malformed payloads cannot be negative facts."""
    try:
        result = json.loads(raw)
    except (ValueError, TypeError, UnicodeError):
        raise IntelligenceError('intelligence_invalid_response') from None
    if not isinstance(result, dict):
        raise IntelligenceError('intelligence_invalid_response')
    return result


def _limit(tool):
    with _LOCK:
        now = time.monotonic()
        interval = 6.0 if tool == 'nvd_lookup' else 0.5
        if now - _LAST.get(tool, -1e6) < interval:
            raise IntelligenceError('intelligence_rate_limited')
        _LAST[tool] = now
    # Optional shared limiter, matching the existing source transport's use of
    # Redis. If Redis is unavailable, keep the stricter local limiter.
    try:
        from redis import Redis
        with Redis.from_url(get_settings().redis_url(), socket_timeout=0.3,
                            socket_connect_timeout=0.3) as client:
            if not client.set('ict:dynamic-source:' + tool, '1', nx=True, px=int(interval * 1000)):
                raise IntelligenceError('intelligence_rate_limited')
    except IntelligenceError:
        raise
    except Exception:
        pass


def fetch(tool, identifier=None, ecosystem=None, package=None, version=None, topic=None, query=None):
    if tool not in _TTL:raise IntelligenceError('intelligence_tool_unavailable')
    if tool=='web_search':return _web_search(query)
    key = (tool, identifier, ecosystem, package, version, topic)
    with _LOCK:
        cached = _CACHE.get(key)
        if cached and time.monotonic() - cached[0] < _TTL[tool]:
            return cached[1], cached[2], True
    deadline = time.monotonic() + 8
    _limit(tool)
    records, note = [], None
    if tool == 'nvd_lookup':
        settings = get_settings()
        headers = {'apiKey': settings.nvd_api_key.get_secret_value()} if settings.nvd_api_key else {}
        raw = _json_request('GET', NVD_URL, params={'cveId': identifier, 'resultsPerPage': 1}, headers=headers, deadline=deadline)
        # A real 404 is distinct from a successful but malformed/empty body.
        payload = {'vulnerabilities': []} if raw is None else _object(raw)
        if not isinstance(payload.get('vulnerabilities'), list):
            raise IntelligenceError('intelligence_invalid_response')
        for item in payload.get('vulnerabilities', []):
            if not isinstance(item,dict) or not isinstance(item.get('cve'),dict):
                raise IntelligenceError('intelligence_invalid_response')
            if item.get('cve', {}).get('id') != identifier:
                raise IntelligenceError('intelligence_entity_mismatch')
            records.append(nvd_record(item))
    elif tool == 'kev_lookup':
        source_url = CISA_URL
        try:
            raw = _json_request('GET', source_url, deadline=deadline)
            if raw is None:
                raise IntelligenceError('intelligence_source_unavailable')
        except IntelligenceError as exc:
            if str(exc) != 'intelligence_source_unavailable':
                raise
            source_url = CISA_MIRROR
            raw = _json_request('GET', source_url, deadline=deadline)
        payload = _object(raw)
        rows = payload.get('vulnerabilities')
        released = payload.get('dateReleased')
        released_at = timestamp(released) if isinstance(released, str) and len(released) <= 100 else None
        if not isinstance(rows, list) or any(not isinstance(x,dict) for x in rows) or released_at is None:
            raise IntelligenceError('intelligence_invalid_response')
        row = next((x for x in rows if x.get('cveID') == identifier), None)
        if row:
            # This is the actual catalogue entry, not a model's interpretation.
            content = json.dumps(row, ensure_ascii=False)
            records.append(RawRecord(external_id=identifier, title='CISA KEV: ' + identifier,
                source_url=source_url, document_type='vulnerability', content=content,
                vulnerability_id=identifier, aliases=[identifier],
                products=[row.get('product', '')], raw=row, is_demo=False,
                source_updated_at=released_at,
                source_fields={'exploitation': {'catalog': 'CISA KEV', 'entry': row}},
                metadata_json={'catalog_version': payload.get('catalogVersion'),
                    'catalog_date_released': payload['dateReleased'], 'upstream_url': source_url},
                content_completeness='full_text'))
        else:
            note = 'kev_no_entry_is_not_proof_of_no_exploitation'
    elif tool == 'osv_lookup':
        raw = _json_request('POST', OSV_URL,
            body={'package': {'ecosystem': ecosystem, 'name': package}, 'version': version}, deadline=deadline)
        payload = _object(raw)
        if not isinstance(payload.get('vulns',[]),list):
            raise IntelligenceError('intelligence_invalid_response')
        if payload.get('next_page_token'):
            note = 'osv_bounded_first_page_not_complete'
        for item in payload.get('vulns', [])[:8]:
            if not isinstance(item,dict):raise IntelligenceError('intelligence_invalid_response')
            affected = item.get('affected')
            if not isinstance(affected, list) or not affected or any(not isinstance(entry, dict) for entry in affected):
                raise IntelligenceError('intelligence_entity_mismatch')
            def matching(entry):
                data = entry.get('package')
                if not isinstance(data, dict) or data.get('ecosystem') != ecosystem or not isinstance(data.get('name'), str):
                    return False
                # PyPI normalizes '.', '_' and '-' and case. Preserve exact
                # names for ecosystems with case-sensitive package identities.
                normalize = lambda value: re.sub(r'[-_.]+', '-', value).lower() if ecosystem == 'PyPI' else value
                return normalize(data['name']) == normalize(package)
            if not any(matching(entry) for entry in affected):
                raise IntelligenceError('intelligence_entity_mismatch')
            records.append(parse_osv(item))
        if len(payload.get('vulns', [])) > 8:
            note = 'osv_result_limit_not_complete'
    elif tool == 'knowledge_lookup':
        url = OWASP[topic]
        raw = _json_request('GET', url, deadline=deadline)
        html = raw.decode('utf-8') if raw else ''
        body = re.search(r'<(?:main|article)\b[^>]*>(.*?)</(?:main|article)>', html, re.S | re.I)
        if not body:
            raise IntelligenceError('intelligence_document_body_missing')
        content = plain(body.group(1))
        # A site-wide advertisement can be the first h1. Prefer the risk
        # heading inside the same body that is actually stored as evidence.
        headings=[plain(text) for text in re.findall(r'<h[1-6]\b[^>]*>(.*?)</h[1-6]>',body.group(1),re.S|re.I)]
        title=next((heading for heading in headings if re.match(r'LLM\s*\d{2}\b',heading,re.I)),
            'OWASP GenAI '+topic)
        if len(content) < 200 or not re.search('LLM|prompt|agency|poison', content, re.I):
            raise IntelligenceError('intelligence_document_body_missing')
        records.append(RawRecord(external_id=url, title=title[:500], source_url=url,
            document_type='standard', content=content[:30000], is_demo=False,
            raw={'html': html}, metadata_json={'publisher': 'OWASP', 'lookup_topic': topic,
                'content_truncated': len(content) > 30000},
            content_completeness='partial' if len(content) > 30000 else 'full_text'))
    else:
        raise IntelligenceError('intelligence_tool_unavailable')
    checked = utc_now().isoformat()
    value = {'records': records, 'note': note}
    with _LOCK:
        _CACHE[key] = (time.monotonic(), value, checked)
        while len(_CACHE) > 128:
            _CACHE.popitem(last=False)
    return value, checked, False


def lookup(db, args):
    value, checked, cached = fetch(**args)
    records = value['records']
    tool = args['tool']
    source = db.scalar(select(DataSource).where(DataSource.name == 'Agent on-demand ' + tool).with_for_update())
    if source is None:
        adapter = {'nvd_lookup':'nvd', 'kev_lookup':'kev', 'osv_lookup':'osv', 'knowledge_lookup':'owasp','web_search':'web_search'}[tool]
        source = DataSource(name='Agent on-demand ' + tool,
            adapter=adapter, category='web' if tool=='web_search' else 'standard' if tool == 'knowledge_lookup' else 'vulnerability',
            enabled=False, config={}, cursor={})
        db.add(source)
        db.flush()
    task = TaskRun(source_id=source.id, status='succeeded', kind='agent_lookup',
        attempts=1, started_at=utc_now(), finished_at=utc_now(), result={})
    db.add(task)
    db.flush()
    counts = ingest_batch(db, source, CollectionBatch(records, dict(source.cursor)), task)
    task.result = {**counts, 'tool': tool, 'checked_at': checked, 'cache_hit': cached,
                   'limitation': value['note']}
    db.commit()
    documents=[]
    from backend.api.v1.tools import document_json
    from backend.models import DocumentRevision
    for record in records:
        document=db.scalar(select(Document).where(Document.source_id==source.id,
            Document.external_id==record.external_id, Document.deleted_at.is_(None)))
        if document:
            revision=db.scalar(select(DocumentRevision).where(DocumentRevision.document_id==document.id,
                DocumentRevision.content_hash==document.content_hash))
            documents.append({**document_json(db, document), 'is_current': True,
                'revision': revision.payload, 'content': revision.payload['content'],
                'version_created_at': revision.created_at, 'operation': 'add'})
    return {'schema_version':'1', 'status':'completed' if documents else 'empty',
            'documents':documents, 'checked_at':checked, 'cache_hit':cached,
            'limitation':value['note'], 'tool':tool}
