"""Summarize numeric-only candidate output without disclosing engine/config paths."""
import argparse
import json
from pathlib import Path


def rate(rows):
    rows = [r for r in rows if r['tag'] == 'measure']
    if not rows:
        return {}
    e = [r['engine'] for r in rows]
    hits = sum(x.get('hits',0) for x in e)
    lookups = sum(x.get('lookups',0) for x in e)
    offloaded = sum(x['offloaded'] for x in e) if all('offloaded' in x for x in e) else None
    routed = lookups + offloaded if offloaded is not None else None
    return dict(requests=len(rows),
                mean_wall_s=sum(r['wall_s'] for r in rows)/len(rows),
                mean_ttft_s=sum(r.get('first_token_s') or 0 for r in rows)/max(1,sum(r.get('first_token_s') is not None for r in rows)),
                prefill_tps=1000*sum(x['prompt_tokens'] for x in e)/sum(x['prompt_ms'] for x in e),
                decode_tps=1000*sum(r['emitted'] for r in rows)/sum(x['decode_ms'] for x in e),
                output_tokens=sum(r['emitted'] for r in rows),
                draft_acceptance=sum(x.get('drafts_accepted',0) for x in e)/max(1,sum(x.get('drafts_offered',0) for x in e)),
                expert_hit_fraction=sum(x.get('hits',0) for x in e)/max(1,sum(x.get('lookups',0) for x in e)),
                routed_vram_fraction=hits/max(1,routed) if routed is not None else None,
                routed_offload_fraction=offloaded/max(1,routed) if routed is not None else None,
                routed_cpu_fraction=(lookups-hits)/max(1,routed) if routed is not None else None,
                file_mb=sum(x.get('file_mb',0) for x in e),
                system_data_disk_read_mib=sum((r['after']['disk'].get('sdd',[0,0,0])[2]-r['before']['disk'].get('sdd',[0,0,0])[2])*512/2**20 for r in rows),
                process_read_bytes=(sum(r['process_io_delta']['read_bytes'] for r in rows)
                                    if all('read_bytes' in r.get('process_io_delta',{}) for r in rows) else None),
                swapped_out_pages=sum(r['after'].get('pswpout',0)-r['before'].get('pswpout',0) for r in rows),
                repeat_comparisons=sum(r.get('within_arm_repeat_matches') is not None for r in rows),
                repeat_matches=sum(r.get('within_arm_repeat_matches') is True for r in rows),
                output_comparisons=sum('output_matches_reference' in r for r in rows),
                output_matches=sum(r.get('output_matches_reference',False) for r in rows))


def summary(data):
    out = []
    for a in data['arms']:
        row = dict(name=a['spec']['name'],completed=a.get('completed',False),
                   context=a['spec'].get('context',32768),load_s=a.get('load_s'),
                   anon_huge_kib=a.get('anon_huge_kib'),all=rate(a['rows']),
                   domains={f:rate([r for r in a['rows'] if r['fixture']==f])
                            for f in sorted(set(r['fixture'] for r in a['rows'] if r['tag']=='measure'))})
        # Occurrence order is fixed within an arm; show first/warm passes separately.
        seen = {}
        passes = {}
        for r in a['rows']:
            if r['tag'] != 'measure':
                continue
            n = seen.get(r['fixture'], 0)
            seen[r['fixture']] = n + 1
            passes.setdefault(n, []).append(r)
        row['passes'] = {str(k): rate(v) for k, v in passes.items()}
        out.append(row)
    return out


if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('results',type=Path);p.add_argument('--out',type=Path);a=p.parse_args()
    s=summary(json.loads(a.results.read_text()))
    if a.out:a.out.write_text(json.dumps(s,indent=2))
    for r in s:
        x=r['all']
        print(r['name'],r['completed'], 'PF',round(x.get('prefill_tps',0),2),
              'decode',round(x.get('decode_tps',0),2),'accept',round(x.get('draft_acceptance',0),3),
              'file MB',x.get('file_mb'),'load',round(r['load_s'] or 0,1))
