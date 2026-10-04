"""Parse native profiling counters without reading or saving response content."""
import argparse
import json
from pathlib import Path
import re
import statistics

DECODE = re.compile(r'(\d+) windows, avg T ([\d.]+), ([\d.]+) tokens/window, ([\d.]+) ms/window = verify ([\d.]+) \(GPU-reach wait ([\d.]+) \+ per-layer host ([\d.]+) \[plan ([\d.]+) actq ([\d.]+) jobs ([\d.]+) CPU ([\d.]+)\] \+ stage ([\d.]+)\) \+ commit/emit ([\d.]+) \+ draft ([\d.]+); per layer-window: CPU experts ([\d.]+) \(([\d.]+) entries\), VRAM hits ([\d.]+), PCIe ([\d.]+)')
DNAMES = ['windows','avg_T','tokens_per_window','window_ms','verify_ms','gpu_reach_wait_ms','host_per_layer_sum_ms','plan_ms','actq_ms','jobs_ms','cpu_run_ms','host_stage_ms','commit_emit_ms','draft_ms','cpu_experts_per_layer_window','cpu_entries_per_layer_window','vram_hits_per_layer_window','pcie_experts_per_layer_window']

def parse(row, capacity=6):
    out={k:row[k] for k in ('arm','tag','fixture','repeat','pcie_frac','input_tokens','emitted_tokens','ttft_native_s','wall_s')}
    e=row['engine']; out['prefill_tps']=e.get('prompt_read',e['prompt_tokens'])/e['prompt_ms']*1000
    out['decode_tps']=e['generated']/e['decode_ms']*1000
    out['native_hit_ratio_pct']=100*e.get('hits',0)/max(1,e.get('lookups',0))
    out['draft_accept_pct']=100*e.get('drafts_accepted',0)/max(1,e.get('drafts_offered',0))
    out['prompt_ms']=e['prompt_ms'];out['decode_ms']=e['decode_ms']
    out['file_blobs']=e.get('file_blobs');out['file_mb']=e.get('file_mb')
    out['resource_delta']={k:row['after'][k]-row['before'][k] for k in ('pswpin','pswpout','pgmajfault')}
    for line in row['diagnostic_lines']:
        if line.startswith('strata decode timing:'):
            m=DECODE.search(line)
            if not m:raise ValueError('Unknown decode timing format')
            out['decode']=dict(zip(DNAMES,map(float,m.groups())))
        elif line.startswith('strata decode GPU stages'):
            m=re.search(r'total ([\d.]+) ms/window over (\d+) windows',line)
            if not m:raise ValueError('Unknown GPU timing format')
            out['gpu_timeline_ms']=float(m[1]);out['gpu_windows']=int(m[2]);out['gpu_stages']={}
            for kind,body in re.findall(r'(GDN|QSA) layers:(.*?)(?=\||$)',line):
                out['gpu_stages'][kind]={k.strip():float(v) for k,v in re.findall(r'\s*(.*?)\s+(-?\d+\.\d+)(?=\s|$)',body)}
        elif line.startswith('strata prefill timing:') and 'GPU timeline' in line:
            m=re.search(r'(\d+) tokens, GPU timeline ([\d.]+) ms, wall ([\d.]+) ms, host staging ([\d.]+) ms:(.*)',line)
            if not m:raise ValueError('Unknown prefill timing format')
            p={'tokens':int(m[1]),'timeline_ms':float(m[2]),'wall_ms':float(m[3]),'host_staging_cumulative_ms':float(m[4])}
            p['stages']={k.strip():float(v) for k,v,pct in re.findall(r'\s*(.*?)\s+([\d.]+)\s+\(([\d.]+)%\)',m[5])}
            out.setdefault('prefill',[]).append(p)
    if 'decode' in out:
        d=out['decode']
        d['other_wall_ms']=round(d['window_ms']-d['verify_ms']-d['commit_emit_ms']-d['draft_ms'],3)
    # Native lookups omit entries offloaded over PCIe: hits/(hits+CPU entries)
    # is NOT a cache hit rate. All routed entries = (offered drafts + windows)*48*10.
    windows=out.get('decode',{}).get('windows')
    lower=max(1,e['generated']-e.get('drafts_accepted',0))
    # Without window counters, the final capped window can accept up to 5 extra
    # tokens (the deployed verifier capacity is 6). Keep the uncertainty explicit.
    wlo=windows if windows is not None else lower
    whi=windows if windows is not None else lower+max(0,capacity-1)
    out['expert_hit_pct_bounds']=[100*e.get('hits',0)/((e.get('drafts_offered',0)+w)*480) for w in (whi,wlo)]
    out['expert_hit_pct']=sum(out['expert_hit_pct_bounds'])/2
    out['expert_hit_pct_estimated']=windows is None
    if windows is not None:
        out['routed_entries']=(e.get('drafts_offered',0)+int(windows))*480
        out['pcie_routed_entries']=out['routed_entries']-e.get('lookups',0)
    if 'gpu_windows' in out:
        out['gpu_decode_only']=out['gpu_windows']==int(out['decode']['windows'])
        # Existing verifier counters can include short-prompt processing; never label these decode-only.
        out['gpu_stage_sum_ms']=sum(sum(x.values()) for x in out['gpu_stages'].values())
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('results',type=Path);a=ap.parse_args()
    data=json.loads(a.results.read_text());rows=[parse(r, int(data.get('arms',{}).get(r['arm'],{}).get('engine',{}).get('spec',6))) for r in data['rows']]
    staging_before={}
    for row in rows:
        for p in row.get('prefill',[]):
            cumulative=p['host_staging_cumulative_ms']
            previous=staging_before.get(row['arm'],0.0)
            p['host_staging_delta_ms']=cumulative-previous if cumulative>=previous else None
            staging_before[row['arm']]=cumulative
    groups={}
    for row in rows:
        if row['tag']=='warmup':continue
        key='/'.join(str(row[k]) for k in ('arm','tag','fixture','pcie_frac'))
        groups.setdefault(key,[]).append(row)
    summary={key:{'n':len(rs),**{k:statistics.median(r[k] for r in rs) for k in ('prefill_tps','decode_tps','ttft_native_s','expert_hit_pct','draft_accept_pct')}} for key,rs in groups.items()}
    (a.results.parent/'parsed.json').write_text(json.dumps({'rows':rows,'summary':summary},indent=2))
    print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
