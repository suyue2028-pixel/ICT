"""Measure real contest Q&A and check original citations through the Vue proxy.

This is an integration smoke check, not a scored accuracy or latency benchmark.
Upstream error scenarios belong to the separate offline contract tests.
"""
import argparse
from datetime import datetime, timezone
import json
import time
import urllib.error
import urllib.request
from uuid import uuid4

from run_fullstack import ROOT, LOCAL, OPENER, configuration


CASES = [
    ('definition', 'CVE-2021-44228 是什么漏洞？请说明机制和条件。'),
    ('audit', '智能体工具审计有哪些风险，如何防护？'),
    ('relation', '请结合 OWASP 过度代理资料与已收录的 LangChain 文件访问漏洞，详细说明两类风险的关联依据与成立条件。不要把对照当成已验证攻击链。'),
    ('missing', '请提供 CVE-2099-999999 的真实受影响版本和修复版本；资料缺失时请明确说明，不要猜测。'),
    ('daily', '我喜欢你'),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-live', action='store_true')
    args = parser.parse_args()
    if not args.allow_live:
        parser.error('--allow-live is required; model requests can incur charges')
    env = configuration()
    user = {'Authorization': 'Bearer ' + json.loads((LOCAL / 'session.json').read_text())['access_token']}
    captures, checks = {}, []

    def call(path, body=None, headers=None):
        started = time.monotonic()
        request = urllib.request.Request('http://127.0.0.1:5173' + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Content-Type': 'application/json', **(headers or {})})
        try:
            response = OPENER.open(request, timeout=65)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return (response.code, json.loads(response.read(2 * 1024 * 1024)),
                {k.lower(): v for k, v in response.headers.items()}, round((time.monotonic()-started)*1000))

    def check(name, passed):
        checks.append({'name': name, 'passed': bool(passed)})

    for name, question in CASES:
        status, response, headers, elapsed = call('/api/v1/agent/query',
            {'query': question, 'is_demo': False}, {**user, 'Idempotency-Key': str(uuid4())})
        evidence = response.get('evidence', [])
        captures[name] = {'question': question, 'http': status, 'response': response,
            'headers': {k: v for k, v in headers.items() if k.startswith('x-rag-')},
            'elapsed_ms': elapsed, 'answer_chars': len(response.get('answer', ''))}
        check(name + '_handled', status == 200 and bool(response.get('answer')))
        if name in {'definition', 'audit', 'relation'}:
            check(name + '_real_model_and_saved_sources', status == 200
                and headers.get('x-rag-answer-mode') == 'live' and bool(evidence))
        if name == 'relation':
            check('relation_multiple_documents', len({e['document_id'] for e in evidence}) >= 2)
        elif name == 'definition':
            check('cve_exact_lookup_kept', any(t.get('tool') == 'exact_lookup' for t in response.get('tool_trace', [])))
        elif name == 'daily':
            check('daily_no_fake_evidence', response.get('response_kind') == 'conversation' and not evidence
                and not response.get('sources') and headers.get('x-rag-answer-mode') == 'live')
        elif name == 'missing':
            check('nonexistent_cve_no_fake_details', response.get('status') == 'insufficient_evidence'
                and not evidence and not response.get('sources') and not response.get('assertions'))
        check(name + '_coverage_citation_bounds', all(all(i.isdigit() and 1 <= int(i) <= len(evidence)
            for i in row['evidence_ids']) for row in response.get('requirement_coverage', [])))
        print(json.dumps({'case': name, 'http': status, 'mode': headers.get('x-rag-answer-mode'),
            'elapsed_ms': elapsed, 'answer_chars': captures[name]['answer_chars'],
            'source_documents': len({e['document_id'] for e in evidence})}, ensure_ascii=False), flush=True)

    # Check the hash, locator and original excerpt, without pretending this
    # proves every generated sentence semantically follows from the evidence.
    for name, capture in captures.items():
        for index, item in enumerate(capture['response'].get('evidence', []), start=1):
            status, original, _, _ = call('/api/v1/documents/' + item['document_id'],
                headers={'X-API-Key': env['ADMIN_API_KEY']})
            valid = False
            if status == 200:
                revision = next((r['payload'] for r in original.get('revisions', [])
                    if r['content_hash'] == item['content_hash']), None)
                if revision:
                    kind, _, start, end = item['locator'].split(':')
                    text = original['content'] if kind == 'content' else json.dumps(revision, ensure_ascii=False)
                    valid = text[int(start):int(end)] == item['content'] and original['is_demo'] is False
            check(name + '_original_citation_' + str(index), valid)
    status, denied, _, _ = call('/api/v1/agent/query', {'query': '你好', 'is_demo': False})
    check('login_required', status == 401 and denied.get('error', {}).get('code') == 'user_token_required')
    status, _, _, _ = call('/api/v1/internal/llm/capabilities', headers=user)
    check('ordinary_user_cannot_read_model_configuration', status in {401, 403})

    public = json.dumps(captures, ensure_ascii=False)
    secrets = [v for k, v in env.items() if any(s in k for s in ('KEY', 'TOKEN', 'PASSWORD')) and len(v) > 12]
    for path in [ROOT / '.secrets/llm-api-key', ROOT / '.secrets/tavily-api-key']:
        if path.exists():
            secrets.append(path.read_text().strip())
    if any(key and key in public for key in secrets):
        raise RuntimeError('Server credential appeared in public response; captures not saved')
    check('server_secrets_absent_from_responses', True)
    result = {'timestamp': datetime.now(timezone.utc).isoformat(),
        'mode': 'live_model_and_real_saved_sources', 'checks': checks,
        'passed': sum(c['passed'] for c in checks), 'total': len(checks),
        'limitations': ['Small smoke check, not the contest accuracy evaluation.',
            'Citation identity checks do not replace human semantic review.',
            'Cross-document comparison does not prove an exploited attack chain.',
            'Upstream error injection is tested separately with Mock.']}
    target = ROOT / 'reports/local/contest_answer_validation'
    target.mkdir(parents=True, exist_ok=True)
    (target / 'final_live_captures.json').write_text(public + '\n')
    (target / 'final_live_checks.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'passed': result['passed'], 'total': result['total']}, ensure_ascii=False))
    return 0 if result['passed'] == result['total'] else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, KeyError, OSError):
        print('Real contest Q&A check could not complete; inspect local service status.')
        raise SystemExit(1)
