"""Verify a running --no-mtp server with synthetic requests (no real tool execution).

Checks engine metadata, streaming/nonstreaming, batched and short prompt paths,
checkpoint reuse, and a read tool round trip. Credentials never enter results.
"""
import argparse
import json
from pathlib import Path
import time

import requests


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url', default='http://127.0.0.1:8080')
    p.add_argument('--model', default='qwen3.8-fn')
    p.add_argument('--key-file', required=True, type=Path)
    p.add_argument('--context', type=int, default=262144)
    p.add_argument('--out', required=True, type=Path)
    a = p.parse_args()
    s = requests.Session()
    s.trust_env = False
    s.headers['x-api-key'] = a.key_file.read_text().strip()
    url = a.url.rstrip('/')
    r = s.get(url + '/metrics', timeout=10)
    r.raise_for_status()
    info = r.json()['engine']
    assert info['mtp_enabled'] == 0 and info['spec'] == 1 and info['lookup'] == 0, info
    assert info['max_context'] == a.context
    evidence = {'engine': info, 'checks': []}

    def save(name, data):
        evidence['checks'].append({'name': name, **data})
        a.out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
        print(name, json.dumps(data, ensure_ascii=False), flush=True)

    def post(path, payload):
        r = s.post(url + path, json={'model': a.model, **payload}, timeout=(10, 180))
        r.raise_for_status()
        return r.json()

    # Real decoded text and counters, not just HTTP readiness. The second exact
    # prompt exercises the live/checkpoint path without a drafter's KV to restore.
    prompt = ('Each record has color blue.\n' * 64) + '计算17乘以23，只输出结果。'
    for i in range(2):
        v = post('/v1/chat/completions', {'messages': [{'role': 'user', 'content': prompt}],
                 'temperature': 0, 'max_tokens': 32, 'reasoning_effort': 'none'})
        t = v['timings']
        text = v['choices'][0]['message']['content']
        assert '391' in text, text
        assert t['draft_n'] == 0 and t['draft_n_accepted'] == 0, t
        if i:
            assert t['cache_n'] > 0, t
        save('text-' + str(i), {'text': text, 'timings': t})

    # Messages SSE is the protocol Pi consumes.
    events = []
    payload = {'model': a.model, 'messages': [{'role': 'user', 'content': '只回复你好。'}],
               'max_tokens': 32, 'temperature': 0, 'stream': True, 'thinking': {'type': 'disabled'}}
    with s.post(url + '/v1/messages', json=payload, stream=True, timeout=(10, 180)) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if line.startswith(b'data: '):
                events.append(json.loads(line[6:]))
    text = ''.join(x.get('delta', {}).get('text', '') for x in events)
    assert '你好' in text and any(x.get('type') == 'message_stop' for x in events), events
    save('messages-stream', {'text': text, 'event_types': [x.get('type') for x in events]})

    tools = [{'name': 'read', 'description': 'Read the contents of a synthetic note.',
              'input_schema': {'type': 'object', 'properties': {'path': {'type': 'string'}},
                               'required': ['path'], 'additionalProperties': False}}]
    messages = [{'role': 'user', 'content': '请调用read工具读取notes/acceptance.txt，然后只回复文件中的口令。不要猜测内容。'}]
    params = {'tools': tools, 'max_tokens': 256, 'temperature': 0, 'thinking': {'type': 'disabled'}}
    v = post('/v1/messages', {'messages': messages, **params})
    calls = [x for x in v['content'] if x['type'] == 'tool_use']
    save('tool-request', {'response': v})
    assert v['stop_reason'] == 'tool_use' and len(calls) == 1, v
    call = calls[0]
    assert call['name'] == 'read' and call['input'].get('path') == 'notes/acceptance.txt', call
    messages.extend([{'role': 'assistant', 'content': v['content']},
                     {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': call['id'],
                                                   'content': '口令：蓝鲸-7319'}]}])
    v = post('/v1/messages', {'messages': messages, **params})
    save('tool-result', {'response': v})
    assert v['stop_reason'] == 'end_turn', v
    assert not any(x['type'] == 'tool_use' for x in v['content']), v
    assert '蓝鲸-7319' in ''.join(x.get('text', '') for x in v['content']), v
    evidence['passed'] = True
    a.out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
