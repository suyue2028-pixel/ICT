"""Live semantic routing acceptance through Vue proxy, FastAPI and Agent.

Requires --allow-live; uses the existing private platform key. No fixtures are
inserted. Upstream error injection is covered separately by Mock tests.
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-live', action='store_true')
    args = parser.parse_args()
    if not args.allow_live:
        parser.error('--allow-live is required; requests can incur provider charges')
    env = configuration()
    session = json.loads((LOCAL / 'session.json').read_text())
    user = {'Authorization': 'Bearer ' + session['access_token']}
    checks, captures = [], {}

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
            data = json.loads(response.read(2 * 1024 * 1024))
            return response.code, data, {k.lower(): v for k, v in response.headers.items()}, round((time.monotonic() - started) * 1000)

    def check(name, passed, detail=None):
        checks.append({'name': name, 'passed': bool(passed), 'detail': detail})

    def query(name, question, session_id=None):
        body = {'query': question, 'is_demo': False}
        if session_id:
            body['session_id'] = session_id
        code, data, headers, elapsed = call('/api/v1/agent/query', body,
            {**user, 'Idempotency-Key': str(uuid4())})
        captures[name] = {'question': question, 'http': code, 'response': data,
            'headers': {k: v for k, v in headers.items() if k.startswith('x-rag-')}, 'elapsed_ms': elapsed}
        check(name + '_handled', code == 200, {'http': code, 'status': data.get('status'), 'elapsed_ms': elapsed,
            'mode': headers.get('x-rag-answer-mode'), 'llm_calls': headers.get('x-rag-llm-calls'),
            'retrieval_mode': data.get('retrieval_mode'), 'source_count': len(data.get('evidence', [])),
            'tools': [t['tool'] for t in data.get('tool_trace', [])]})
        return code, data, headers

    for name, question in [('hello', '你好'), ('affection', '我喜欢你'),
            ('identity', '你是谁'), ('arithmetic', '2+2等于几'), ('casual', '今天天气真好')]:
        code, data, headers = query(name, question)
        check(name + '_one_model_call_without_retrieval', code == 200
            and data.get('response_kind') == 'conversation' and data.get('retrieval_mode') == 'conversation'
            and headers.get('x-rag-answer-mode') == 'live' and headers.get('x-rag-llm-calls') == '1'
            and not data.get('evidence') and not data.get('sources') and not data.get('tool_trace')
            and bool(data.get('answer')))
        if name == 'arithmetic':
            check('arithmetic_answer_is_four', '4' in data.get('answer', '') or '四' in data.get('answer', ''))
        print(json.dumps({'case': name, 'http': code, 'kind': data.get('response_kind'),
            'elapsed_ms': captures[name]['elapsed_ms']}, ensure_ascii=False), flush=True)

    code, mixed, headers = query('mixed_cve', '你好，请解释 CVE-2021-44228 是什么漏洞，注明真实来源。')
    check('mixed_greeting_still_professional', code == 200 and mixed.get('response_kind') == 'knowledge'
        and any(t.get('tool') == 'exact_lookup' for t in mixed.get('tool_trace', []))
        and bool(mixed.get('evidence')) and headers.get('x-rag-answer-mode') == 'live')
    if code == 200:
        code, follow, _ = query('cve_followup', '它是否有在野利用记录？', mixed.get('session_id'))
        check('history_target_reaches_official_tools', code == 200
            and any(t.get('tool') == 'kev_lookup' for t in follow.get('tool_trace', [])))
    code, professional, headers = query('local_knowledge', '请概括《LLM04:2025 Data and Model Poisoning》的内容，并注明来源。')
    check('local_security_model_answer_keeps_citations', code == 200 and bool(professional.get('evidence'))
        and professional.get('response_kind') == 'knowledge' and headers.get('x-rag-answer-mode') == 'live')
    code, concept, headers = query('missing_concept', '量子密钥分发 QKD 的一般安全机制和局限是什么？')
    check('missing_concept_does_not_fabricate_sources', code == 200
        and (bool(concept.get('evidence')) or (not concept.get('sources') and concept.get('retrieval_mode') == 'llm_unverified'
            and '模型通用解释；未经过来源核验。' in concept.get('answer', ''))))

    citations = 0
    for case, capture in captures.items():
        data = capture['response']
        n = len(data.get('evidence', []))
        check(case + '_reference_bounds', all(all(i.isdigit() and 1 <= int(i) <= n for i in r['evidence_ids'])
            for r in data.get('requirement_coverage', [])))
        for item in data.get('evidence', []):
            status, original, _, _ = call('/api/v1/documents/' + item['document_id'], headers={'X-API-Key': env['ADMIN_API_KEY']})
            valid = False
            if status == 200:
                revision = next((r['payload'] for r in original.get('revisions', []) if r['content_hash'] == item['content_hash']), None)
                if revision:
                    kind, _, start, end = item['locator'].split(':')
                    text = original['content'] if kind == 'content' else json.dumps(revision, ensure_ascii=False)
                    valid = text[int(start):int(end)] == item['content'] and original['is_demo'] is False
            citations += 1
            check(case + '_original_citation_' + str(citations), valid)

    code, denied, _, _ = call('/api/v1/agent/query', {'query': '你好', 'is_demo': False})
    check('login_still_required', code == 401 and denied.get('error', {}).get('code') == 'user_token_required')
    code, denied, _, _ = call('/api/v1/internal/llm/capabilities', headers=user)
    check('ordinary_user_cannot_read_model_configuration', code in {401, 403})
    keys = [v for k, v in env.items() if any(s in k for s in ('KEY', 'TOKEN', 'PASSWORD')) and len(v) > 12]
    for path in [ROOT / '.secrets/llm-api-key', ROOT / '.secrets/tavily-api-key']:
        if path.exists():
            keys.append(path.read_text().strip())
    public = json.dumps(captures, ensure_ascii=False)
    check('server_secrets_absent_from_responses', not any(key and key in public for key in keys))
    result = {'timestamp': datetime.now(timezone.utc).isoformat(), 'mode': 'live_deepseek_and_real_saved_sources',
        'checks': checks, 'passed': sum(c['passed'] for c in checks), 'total': len(checks),
        'limitations': ['Tavily live search requires a separately configured search key.',
            'Model error injection and provider switching are separate Mock tests.',
            'Automatic citation validation does not replace human semantic review.']}
    (LOCAL / 'semantic_live_validation.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    (LOCAL / 'semantic_live_captures.json').write_text(public + '\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['passed'] == result['total'] else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, KeyError, OSError):
        print('Live semantic verification could not complete; inspect local service status.')
        raise SystemExit(1)
