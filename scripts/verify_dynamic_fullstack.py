"""Small live acceptance run, with credential-free local captures.

Requires explicit --allow-live: uses the existing platform model and real
official sources, adds only returned official records, never seeds fixtures.
"""
import argparse
from datetime import datetime, timezone
import json
import time
import urllib.error
import urllib.request
from uuid import uuid4
from run_fullstack import ROOT, LOCAL, OPENER, configuration


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-live',action='store_true')
    args=parser.parse_args()
    if not args.allow_live:
        parser.error('--allow-live is required; platform requests can incur provider charges')
    env=configuration()
    session=json.loads((LOCAL/'session.json').read_text())
    user={'Authorization':'Bearer '+session['access_token']}
    admin={'X-API-Key':env['ADMIN_API_KEY']}
    tool={'X-Agent-Token':env['AGENT_TOOL_TOKEN']}
    service={'Authorization':'Bearer '+env['AGENT_SERVICE_TOKEN']}
    checks=[]; captures={}

    def call(path,*,headers=None,body=None,origin='http://127.0.0.1:5173'):
        started=time.monotonic()
        request=urllib.request.Request(origin+path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Content-Type':'application/json',**(headers or {})})
        try:
            response=OPENER.open(request,timeout=65)
        except urllib.error.HTTPError as error:
            response=error
        except (OSError,TimeoutError):
            return 0,{'detail':'verification_transport_unavailable'},{},round((time.monotonic()-started)*1000)
        with response:
            try: data=json.loads(response.read(2*1024*1024))
            except (ValueError,UnicodeError): data={'detail':'verification_invalid_json'}
            return response.code,data,{k.lower():v for k,v in response.headers.items()},round((time.monotonic()-started)*1000)

    def check(name,passed,detail=None):
        checks.append({'name':name,'passed':bool(passed),'detail':detail})

    code,data,_,_=call('/api/v1/auth/me',headers=user)
    check('ordinary_user_authenticated',code==200 and data.get('authenticated') is True)
    code,data,_,_=call('/api/v1/internal/llm/capabilities',headers=tool)
    check('platform_backend_capabilities',code==200 and data.get('configured') is True,data if code==200 else {'http':code})
    code,health,_,_=call('/v1/health',headers=service,origin='http://127.0.0.1:9001')
    check('real_agent_embedding_platform_transport',code==200 and health.get('llm_mode')=='live' and health.get('llm_transport')=='backend' and health.get('embedding_mode')=='live',health)
    code,docs,_,_=call('/api/v1/intelligence?page_size=100',headers=admin)
    target=next((row['identifier'] for row in docs.get('items',[]) if row['identifier'].startswith('CVE-')),None)
    if not target:
        raise RuntimeError('No saved real CVE available; no fixture substituted')
    cases=[('hello','你好'),('saved_cve',target+' 是什么漏洞'),
        ('tool_audit','智能体工具审计有哪些风险，如何防护？'),
        ('missing_knowledge','我们未上传的内部系统 ZZZ_private_manual_765 的安全审批步骤是什么？'),
        ('malformed_cve','CVE-2026-12 是什么漏洞'),
        ('package_clarification','OSV PyPI langchain 有哪些漏洞？'),
        ('osv_package','OSV PyPI langchain 0.0.200 的安全风险及修复依据'),
        ('dynamic_cve','CVE-2021-44228 最新 CVSS 评分与在野利用情况是什么？')]
    original_citations=0
    for name,question in cases:
        code,data,headers,elapsed=call('/api/v1/agent/query',headers={**user,'Idempotency-Key':str(uuid4())},
            body={'query':question,'is_demo':False})
        captures[name]={'http':code,'response':data,'headers':{k:v for k,v in headers.items() if k.startswith('x-rag-')},'elapsed_ms':elapsed}
        check(name+'_handled',code==200,{'http':code,'status':data.get('status'),'elapsed_ms':elapsed,
            'mode':headers.get('x-rag-answer-mode'),'llm_calls':headers.get('x-rag-llm-calls'),
            'tools':[t['tool'] for t in data.get('tool_trace',[])],'source_count':len(data.get('evidence',[]))})
        if name=='hello':check('hello_real_platform_model',code==200 and data.get('response_kind')=='conversation' and headers.get('x-rag-answer-mode')=='live' and not data.get('evidence'))
        if name=='saved_cve':check('saved_cve_real_model_with_sources',code==200 and bool(data.get('evidence')) and headers.get('x-rag-answer-mode')=='live')
        if name in {'missing_knowledge','malformed_cve','package_clarification'}:
            check(name+'_no_fabricated_sources',code==200 and not data.get('evidence'))
        if name=='tool_audit':
            check('tool_audit_has_sources_and_coverage',code==200 and bool(data.get('evidence')) and bool(data.get('requirement_coverage')))
            check('tool_audit_real_verified_model_answer',code==200 and headers.get('x-rag-answer-mode')=='live')
        if name=='dynamic_cve':
            check('agent_executed_registered_external_tools',any(t.get('tool') in {'nvd_lookup','kev_lookup'} for t in data.get('tool_trace',[])))
            check('dynamic_sources_returned_or_failure_disclosed',bool(data.get('evidence')) or bool(data.get('limitations')))
        if name=='osv_package':
            check('agent_executed_explicit_osv_query',any(t.get('tool')=='osv_lookup' for t in data.get('tool_trace',[])))
        for evidence in data.get('evidence',[]):
            status,original,_,_=call('/api/v1/documents/'+evidence['document_id'],headers=admin)
            valid=False
            if status==200:
                revision=next((r['payload'] for r in original.get('revisions',[]) if r['content_hash']==evidence['content_hash']),None)
                if revision:
                    kind,_,start,end=evidence['locator'].split(':')
                    text=original['content'] if kind=='content' else json.dumps(revision,ensure_ascii=False)
                    valid=text[int(start):int(end)]==evidence['content'] and original['is_demo'] is False
            original_citations+=1
            check(name+'_original_citation_'+str(original_citations),valid)
        n=len(data.get('evidence',[]))
        check(name+'_coverage_reference_bounds',all(
            all(isinstance(i,str) and i.isdigit() and 1<=int(i)<=n for i in r.get('evidence_ids',[]))
            and (r['status']!='missing' or not r.get('evidence_ids')) for r in data.get('requirement_coverage',[])))
    for name,headers in [('missing',{}),('invalid',{'Authorization':'Bearer invalid-local-test'})]:
        code,data,_,_=call('/api/v1/agent/query',headers=headers,body={'query':'你好','is_demo':False})
        check(name+'_user_auth_rejected',code==401 and data.get('error',{}).get('code') in {'user_token_required','invalid_user_token'})
    code,_,_,_=call('/api/v1/internal/llm/capabilities',headers=user)
    check('user_token_cannot_read_internal_model_configuration',code in {401,403})
    secrets=[v for k,v in env.items() if ('TOKEN' in k or 'PASSWORD' in k or 'KEY' in k) and len(v)>12]
    keyfile=ROOT/'.secrets/llm-api-key'
    if keyfile.exists():secrets.append(keyfile.read_text().strip())
    public=json.dumps(captures,ensure_ascii=False)
    check('platform_credentials_absent_from_public_answers',not any(v and v in public for v in secrets))
    result={'timestamp':datetime.now(timezone.utc).isoformat(),'mode':'live_platform_and_official_sources',
        'checks':checks,'passed':sum(c['passed'] for c in checks),'total':len(checks),
        'limitations':['Qwen real API not called: no Qwen credential configured.',
            'Injected auth/timeout/rate-limit behavior belongs to isolated Mock tests.',
            'Semantic correctness still requires independent human source review.']}
    (LOCAL/'dynamic_live_validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    (LOCAL/'dynamic_live_captures.json').write_text(public+'\n')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['passed']==result['total'] else 1


if __name__=='__main__':
    try:raise SystemExit(main())
    except (RuntimeError,ValueError,KeyError,OSError):
        print('Live validation could not complete; inspect credential-free local reports and service status.')
        raise SystemExit(1)
