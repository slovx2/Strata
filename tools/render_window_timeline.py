"""Render numeric-only, real synchronized decode windows (no reconstructed averages)."""
import argparse
import json
import math
import statistics
from pathlib import Path

STAGES = ['', 'hc-read0', 'q8 + qkv', 'conv', 'ab', 'z', 'recurrent',
          'q8 + kv-index', 'k/v + RoPE', 'KV append', 'q + q-index',
          'scores + topk', 'KV resolve', 'attention', 'gate', '', 'out projection',
          'hc-read1 + router', 'shared + quant', 'waitA: CPU plan', 'VRAM experts',
          'waitB: expert delivery', 'PCIe experts compute', 'waitCPU: expert result',
          'copy + combine']


def convert(record, row):
    r = record
    assert r['pcie_frac'] == .25
    assert len(r['gpu_ns']) == 48 * 33 + 4
    assert len(r['copy_ns']) == 96
    assert all(len(r[k]) == 48 for k in ('copy_bytes', 'copy_count', 'layer_kind'))
    events = []

    def add(lane, name, layer, a, b, category, **extra):
        assert all(math.isfinite(x) for x in (a, b)) and b >= a, (name, a, b)
        # A calibrated GPU endpoint can lie a little outside the host envelope.
        assert a >= -r['alignment_error_us'] - 10
        assert b <= r['duration_us'] + r['alignment_error_us'] + 10
        events.append(dict(lane=lane, name=name, layer=layer, start=a / 1000,
                           end=b / 1000, category=category, **extra))

    for lane, name, layer, a, b in r['spans']:
        cat = 'phase' if lane == 'Window' else 'cpu'
        if lane == 'Copy submit':
            cat = 'submit'
        if name.startswith('wait') or name == 'adapt_join':
            cat = 'hostwait'
        if name == 'trace_clock_setup':
            cat = 'trace'
        add(lane, name, layer, a, b, cat)
    stamps = r['gpu_ns']

    def mapped(ns):
        return ns / 1000 + r['gpu_offset_us']

    for layer in range(48):
        base = layer * 33
        prev = stamps[base]
        assert prev > 0
        for i in range(1, 25):
            stamp = stamps[base+i]
            if not stamp:
                continue
            assert stamp >= prev, (layer, i, stamp, prev)
            if i == 21:
                boundary = stamps[base+25]
                assert prev <= boundary <= stamp
                add('GPU', 'waitB: delivery flag', layer, mapped(prev), mapped(boundary), 'gpuwait')
                name = 'expert fetch kernel + rebase' if r['pcie_mode'] == 2 else 'after delivery flag'
                add('GPU', name, layer, mapped(boundary), mapped(stamp),
                    'gpucopy' if r['pcie_mode'] == 2 else 'gap')
                if r['pcie_mode'] == 2:
                    add('Expert transfer', name, layer, mapped(boundary), mapped(stamp), 'copy',
                        bytes=r['copy_bytes'][layer], copies=r['copy_count'][layer])
                prev = stamp
                continue
            add('GPU', STAGES[i] or f'stage {i}', layer, mapped(prev), mapped(stamp),
                'gpuwait' if i in (19, 21, 23) else 'gpu')
            prev = stamp
        assert stamps[base+24] > 0
        after = stamps[(layer+1)*33]
        if after:
            add('GPU', 'layer gap' if layer < 47 else 'before head', layer,
                mapped(prev), mapped(after), 'gap')
        a, b = r['copy_ns'][layer*2:layer*2+2]
        if r['pcie_mode'] == 0 and r['copy_count'][layer]:
            assert a > 0 and b >= a
            add('Expert transfer', 'DMA expert copy batch', layer, mapped(a), mapped(b), 'copy',
                bytes=r['copy_bytes'][layer], copies=r['copy_count'][layer])
        else:
            assert a == b == 0
    base = 48*33
    if stamps[base] and stamps[base+1]:
        add('GPU', 'head', -1, mapped(stamps[base]), mapped(stamps[base+1]), 'gpu')
    sums = {}
    for e in events:
        sums[e['category']] = sums.get(e['category'], 0) + e['end']-e['start']
    return dict(request=r['request'], window=r['window'], fixture=row['fixture'],
                T=r['T'], emitted=r['emitted'], duration=r['duration_us']/1000,
                alignment=r['alignment_error_us']/1000, events=events, sums=sums,
                copyMiB=sum(r['copy_bytes'])/2**20, kinds=r['layer_kind'], mode=r['pcie_mode'])


def report(capture):
    results = json.loads((capture/'results.json').read_text())
    assert results['completed'] and results['config_unchanged']
    rows = {r['request']: r for r in results['rows']}
    windows = [convert(r, rows[r['request']])
               for r in map(json.loads, (capture/'windows.jsonl').read_text().splitlines())
               if rows[r['request']]['tag'] == 'measure']
    assert windows
    groups = {}
    for fixture in sorted(set(w['fixture'] for w in windows)):
        subset = [w for w in windows if w['fixture'] == fixture]
        median = statistics.median(w['duration'] for w in subset)
        representative = min(subset, key=lambda w: abs(w['duration']-median))
        groups[fixture] = dict(count=len(subset), median=median,
                              minimum=min(w['duration'] for w in subset),
                              maximum=max(w['duration'] for w in subset),
                              representative=[representative['request'], representative['window']],
                              gpuWaitMedian=statistics.median(w['sums'].get('gpuwait',0) for w in subset),
                              fetchMedian=statistics.median(w['sums'].get('copy',0) for w in subset),
                              copyMiBMedian=statistics.median(w['copyMiB'] for w in subset))
    # Explicit public allowlist. Engine metadata/logs are deliberately not exported.
    return dict(context=results['context'], fraction=.25, windows=windows, groups=groups,
                requests=[{**{k: r[k] for k in ('request','fixture','emitted','wall_s')},
                           **{k: r['engine'][k] for k in ('prompt_tokens','prompt_ms','decode_ms',
                                                        'drafts_accepted','drafts_offered','file_mb')}}
                          for r in rows.values() if r['tag']=='measure'])


def main():
    p=argparse.ArgumentParser()
    p.add_argument('capture', type=Path)
    p.add_argument('output', type=Path)
    a=p.parse_args()
    data=report(a.capture)
    template=Path(__file__).with_name('window_timeline.html').read_text()
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(template.replace('__TRACE_DATA__', json.dumps(data, ensure_ascii=True)))
    print(json.dumps(data['groups'], indent=2))


if __name__ == '__main__':
    main()
