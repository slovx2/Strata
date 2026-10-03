"""Fast fixed single-turn HTTP benchmark. No conversation history or model downloads.

Use a server with --max-context 32768 --prompt-cache 0. Each invocation warms
the text paths, then measures fresh short/1K/4K requests. --image adds an image
request followed by its cached repeat, both independent conversations. Restart
the service before comparing the first image's encoding/loading cost.
"""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import statistics
import time

import requests


def request(session, url, prompt, output_tokens):
    payload = {
        'model': 'qwen3.8-fn', 'temperature': 0, 'reasoning_effort': 'none',
        'max_tokens': output_tokens, 'stream': True,
        'messages': [{'role': 'system', 'content': 'Follow the user instructions.'},
                     {'role': 'user', 'content': prompt}],
    }
    start = time.perf_counter()
    first = None
    text = ''
    last = {}
    with session.post(url + '/v1/chat/completions', json=payload,
                      stream=True, timeout=(10, 180)) as response:
        response.raise_for_status()
        for line in response.iter_lines(chunk_size=1):
            if not line.startswith(b'data: '):
                continue
            raw = line[6:]
            if raw == b'[DONE]':
                break
            last = json.loads(raw)
            for choice in last.get('choices', []):
                delta = choice.get('delta', {})
                content = delta.get('content') or delta.get('reasoning_content') or ''
                if content and first is None:
                    first = time.perf_counter() - start
                text += content
    timings = last.get('timings') or {}
    if not timings or not text:
        raise RuntimeError('Response missing text or engine timings')
    if timings.get('cache_n', 0):
        raise RuntimeError('Prefix reuse detected: run the server with --prompt-cache 0')
    return {'ttft_s': first, 'wall_s': time.perf_counter() - start,
            'text': text, 'text_sha256': hashlib.sha256(text.encode()).hexdigest(),
            'timings': timings, 'usage': last.get('usage'),
            'finish_reason': last.get('choices', [{}])[0].get('finish_reason')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8080')
    parser.add_argument('--key-file', required=True)
    parser.add_argument('--fixtures', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--repeats', default=1, type=int)
    parser.add_argument('--image', action='store_true')
    args = parser.parse_args()
    fixture_bytes = args.fixtures.read_bytes()
    fixtures = json.loads(fixture_bytes)
    session = requests.Session()
    session.trust_env = False
    session.headers['x-api-key'] = Path(args.key_file).read_text().strip()
    health = session.get(args.url + '/health', timeout=10)
    health.raise_for_status()
    metrics = session.get(args.url + '/metrics', timeout=10)
    metrics.raise_for_status()
    ctx = metrics.json().get('engine', {}).get('max_context')
    if int(ctx or 0) != 32768:
        raise RuntimeError(f'Expected 32768 context, got {ctx}')
    result = {'fixture_sha256': hashlib.sha256(fixture_bytes).hexdigest(),
              'context': ctx, 'repeats': args.repeats,
              'health': health.json(), 'before': metrics.json(), 'rows': []}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()

    def run(name, prompt, tokens, expected):
        row = request(session, args.url, prompt, tokens)
        row.update(name=name, passed=all(x in row['text'] for x in expected))
        result['rows'].append(row)
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps({k: row[k] for k in ['name', 'ttft_s', 'wall_s', 'timings', 'passed']},
                         ensure_ascii=False), flush=True)
        if not row['passed']:
            raise RuntimeError(f'Answer check failed for {name}: {row["text"][:250]}')

    run('warmup', fixtures['short']['prompt'], 32, ['391'])
    run('warmup-medium', fixtures['medium1k']['prompt'], 32, fixtures['medium1k']['expected'])
    run('warmup-long', fixtures['long4k']['prompt'], 32, fixtures['long4k']['expected'])
    for i in range(args.repeats):
        for name in ['short', 'medium1k', 'long4k']:
            f = fixtures[name]
            run(f'{name}-{i+1}', f['prompt'], 128, f['expected'])
    if args.image:
        path = args.fixtures.parent / fixtures['image']['file']
        data = base64.b64encode(path.read_bytes()).decode()
        prompt = [{'type': 'text', 'text': fixtures['image']['prompt']},
                  {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + data}}]
        for name in ['image-first', 'image-cached']:
            run(name, prompt, 128, fixtures['image']['expected'])
    result['total_s'] = time.perf_counter() - started
    result['after'] = session.get(args.url + '/metrics', timeout=10).json()
    result['summary'] = {}
    for name in ['short', 'medium1k', 'long4k']:
        rows = [r for r in result['rows'] if r['name'].startswith(name+'-')]
        result['summary'][name] = {
            'prefill_tps': statistics.median(r['timings']['prompt_per_second'] for r in rows),
            'decode_tps': statistics.median(r['timings']['predicted_per_second'] for r in rows),
            'ttft_s': statistics.median(r['ttft_s'] for r in rows),
            'prompt_n': [r['timings']['prompt_n'] for r in rows],
            'output_n': [r['timings']['predicted_n'] for r in rows],
        }
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({'total_s': result['total_s'], 'summary': result['summary']}), flush=True)


if __name__ == '__main__':
    main()
