"""Isolated, content-free profiling of an existing Song PC deployment.

Uses the deployed native engine and its existing CUDA-graph timestamp probes.
No production config writes, downloads, API key reads, or real conversations.
Results contain timings/counters only. All test inputs are synthetic and public.
Run under direct-env.sh with the deployment Python. A stopped production service
is required. Child engines are closed before this foreground runner exits.
"""
from __future__ import annotations
import argparse
import copy
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tools')]
from serve.server import StrataEngine, engine_args, child_env
from calibrate import chat_ids, with_arg
import strata_tokenizer as ST

PROBES = ('STRATA_DECODE_TIMING', 'STRATA_VERIFY_PROFILE', 'STRATA_PREFILL_TIMING')

def fixtures(tok):
    domains = {
        'go_code': ('Review this synthetic Go service. Explain cancellation, race conditions, backpressure, and write improved Go code with tests. Give at least 30 detailed points.',
          lambda i: f'// service {i}, workers={2+i%11}, queue={32+i%97}\nfunc route{i}(ctx context.Context, in <-chan Job, out chan<- Result) {{ for j := range in {{ r := process(j); select {{ case out <- r: case <-ctx.Done(): return }} }} }}\n'),
        'network': ('Analyze these synthetic network observations. Discuss TCP retransmission, DNS caching, TLS, MTU and load balancing. Propose a step-by-step diagnosis with at least 30 checks.',
          lambda i: f'flow={i} subnet=10.{i%250}.{i*7%250}.0/24 transport={"TCP" if i%2 else "UDP"} rtt_ms={4+i%91} retransmits={i%13} mtu={1280+i%221} tls_ms={11+i%70} dns_ms={i%31} upstream=service-{i%17} status={200 if i%5 else 502}\n'),
        'zh_story': ('根据以下虚构故事笔记续写中文小说。保持人物关系和时间线，描写动作与对话，至少写1500字，不要重复原文。',
          lambda i: f'第{i}条笔记：{["林舟","陈晴","周远","叶青"][i%4]}在{["旧码头","山间小屋","图书馆","雨夜车站","海边集市"][i%5]}发现了{["手写地图","蓝色信封","停走的怀表","一本航海日记"][i%4]}。日期是秋季第{i%89+1}天，距离上次见面{i%23+1}天。他们谈论{["失踪的船只","童年的约定","尚未寄出的信","即将来临的风暴","故乡的变化","一段被误解的往事"][i%6]}，决定先核对线索再行动。\n'),
        'math': ('Use the synthetic exercises as examples to explain modular arithmetic, graph invariants, induction and probability. Give detailed derivations for at least 20 examples, not just final numbers.',
          lambda i: f'Exercise {i}: graph vertices={5+i%43}, edges={9+i%89}; a={i%31+2}, b={i%29+3}, modulus={i%47+5}; recurrence x[n+1]=({i%7+2}*x[n]+{i%11+1}) mod {i%47+5}; prove a bound and compute first 5 terms from x[0]={i%9}.\n'),
        'en_science': ('Compare mechanisms in these fictional research notes. Discuss experimental design, uncertainty, metabolism, ecosystems and material properties. Write a detailed review with at least 30 distinct observations.',
          lambda i: f'Experiment {i}: { ["enzyme kinetics","soil nitrogen","polymer diffusion","leaf transpiration","microbial growth","thermal conductivity"][i%6]}; sample={i%83+10}, temperature={12+i%25} C, concentration={i%17+1} mM, control={i%11+3}, treatment={i%19+7}, duration={i%71+5} h. Distinguish correlation from mechanism and suggest a controlled follow-up.\n'),
        'mixed_structured': ('Produce a detailed technical handover: summarize the multilingual records, then generate a JSON schema and a Python validator with tests. At least 100 lines; do not invoke any actual tools.',
          lambda i: json.dumps({'record':i, 'lang':['zh','en','ja','es','de'][i%5], 'message':['请求等待连接池','retry budget exhausted','接続を再試行します','validar el esquema','Zeitüberschreitung'][i%5], 'tool':['read','search','calculate','write'][i%4], 'args':{'synthetic_id':i,'limit':i%43+1}, 'latency_ms':i*17%1000}, ensure_ascii=False)+'\n'),
    }
    result = {}
    for name, (instruction, line) in domains.items():
        body = ''.join(line(i) for i in range(600))
        for budget in ([256,4096,16384] if name == 'network' else [4096]):
            prefix = 'Synthetic benchmark records:\n'
            suffix = '\nTASK:\n'+instruction
            lo, hi = 0, len(body)
            while lo < hi:
                mid = (lo+hi+1)//2
                if len(chat_ids(tok, prefix+body[:mid]+suffix)) <= budget: lo=mid
                else: hi=mid-1
            prompt = prefix+body[:lo]+suffix
            ids = chat_ids(tok,prompt)
            result[f'{name}_{budget}'] = {'ids':ids, 'prompt':prompt, 'domain':name, 'target':budget}
    return result


def proc_snapshot():
    out = {}
    for filename, fields in [('meminfo',('MemAvailable','SwapFree','Dirty')),('vmstat',('pswpin','pswpout','pgmajfault'))]:
        lines = Path('/proc/'+filename).read_text().splitlines()
        for line in lines:
            p=line.replace(':','').split()
            if p and p[0] in fields: out[p[0]]=int(p[1])
    # Whole-machine counters: these are not attributed to this process alone.
    out['cpu_jiffies'] = [int(x) for x in Path('/proc/stat').read_text().splitlines()[0].split()[1:]]
    out['disk']={p[2]:[int(v) for v in p[3:]] for line in Path('/proc/diskstats').read_text().splitlines() if (p:=line.split()) and p[2].startswith('sd')}
    return out


class Monitor:
    def __init__(self,path):
        self.path=path; self.stop=threading.Event(); self.label='loading'
        self.thread=threading.Thread(target=self.run,daemon=True)
    def run(self):
        with self.path.open('w') as f:
            while not self.stop.is_set():
                row={'time':time.time(),'label':self.label,**proc_snapshot()}
                try:
                    r=subprocess.run(['/usr/lib/wsl/lib/nvidia-smi','--query-gpu=utilization.gpu,utilization.memory,memory.used,power.draw,temperature.gpu,clocks.sm,clocks.mem','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=3)
                    if r.returncode==0: row['gpu']=[float(v.strip()) for v in r.stdout.strip().split(',')]
                except (subprocess.TimeoutExpired,ValueError): pass
                f.write(json.dumps(row)+'\n');f.flush()
                self.stop.wait(1)
    def close(self):
        self.stop.set();self.thread.join(timeout=5)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--config',type=Path,required=True)
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--arms',default='baseline,profile,baseline_after')
    ap.add_argument('--repeats',type=int,default=2)
    ap.add_argument('--pcie-fracs',default='0.0,0.25,0.55,1.0')
    a=ap.parse_args(); os.umask(0o077)
    fractions=tuple(float(v) for v in a.pcie_fracs.split(','))
    assert fractions and all(0<=v<=1 for v in fractions)
    a.out.mkdir(parents=True,exist_ok=True)
    if not str(a.out.resolve()).startswith('/mnt/data/'):
        raise SystemExit('Results must be on /mnt/data')
    # Only read configuration; credentials are removed immediately and never used.
    cfg=json.loads(a.config.read_text());cfg.pop('api_key',None)
    original_config=a.config.read_bytes()
    if cfg.get('expert_profile_save') or '--expert-profile-save' in cfg.get('args',[]):
        raise SystemExit('Persistent expert-profile writes must be disabled for isolated profiling')
    if subprocess.run(['systemctl','is-active','--quiet','strata-sc117']).returncode==0:
        raise SystemExit('Production service is running; wait for an idle maintenance window')
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():continue
        try:
            if (proc/'exe').resolve()==Path(cfg['exe']).resolve():
                raise SystemExit('Another instance of this native engine is running; stop the isolated test')
        except (FileNotFoundError,PermissionError,OSError):pass
    tpath=Path(cfg['tokenizer']);vocab=json.loads((tpath/'vocab.json').read_text());v=[None]*len(vocab)
    for t,i in vocab.items():v[i]=t
    tok=ST.Tokenizer(v,(tpath/'merges.txt').read_text().split('\n'),json.loads((tpath/'token_type.json').read_text()))
    fs=fixtures(tok)
    (a.out/'synthetic-fixtures.json').write_text(json.dumps({k:{'prompt':f['prompt'],'tokens':len(f['ids'])} for k,f in fs.items()},ensure_ascii=False,indent=2))
    result={'schema':1,'context':32768,'output_limit':192,'thinking':False,'prefix_cache':False,'production_initially_active':False,'arms':{},'rows':[], 'notes':['Synthetic prompts only; no generated content, token IDs, API credentials or content hashes persisted.', 'Native engine measurement, excludes HTTP/network and tokenizer latency.', 'GPU stage intervals include waits; host and GPU intervals overlap and must not be added.', '32K capacity with 256/4K/16K occupied inputs; not a 256K production benchmark.']}
    def save():
        tmp=a.out/'results.tmp';tmp.write_text(json.dumps(result,indent=2));tmp.replace(a.out/'results.json')
    mon=Monitor(a.out/'resources.jsonl');mon.thread.start();engine=None
    def terminate(sig,frame): raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,terminate)
    signal.signal(signal.SIGINT,terminate)
    try:
        for arm in a.arms.split(','):
            assert arm in ('baseline','profile','baseline_after','sweep')
            c=copy.deepcopy(cfg)
            c['args']=with_arg(with_arg(c['args'],'--max-context','32768'),'--prompt-cache','0')
            # Exact production settings otherwise, including MTP, adaptive cache and vision reservation.
            c['env']={k:v for k,v in c.get('env',{}).items() if k not in PROBES}
            if arm=='profile':c['env'].update({k:'1' for k in PROBES})
            elif arm=='sweep':c['env']['STRATA_DECODE_TIMING']='1'  # counters already accumulated; no GPU probes
            env=child_env(c)
            for k in list(env):
                if (k.startswith('STRATA_') and any(x in k for x in ('DEBUG','DUMP','STATE_HASH','LOGPOS','DIAGNOSTIC'))) or k in PROBES:env.pop(k,None)
            env.update(c['env'])
            for k in list(env):
                if k.startswith('STRATA_') and any(x in k for x in ('DEBUG','DUMP','STATE_HASH','LOGPOS','DIAGNOSTIC')):
                    env.pop(k,None)
            log=a.out/(arm+'-engine.log'); c['log']=str(log)
            (a.out/(arm+'-config.json')).write_text(json.dumps(c,indent=2))
            print('START',arm,flush=True);t0=time.monotonic();mon.label=arm+'/loading'
            engine=StrataEngine.__new__(StrataEngine)
            engine.__init__(c['exe'],engine_args(c),cwd=c.get('cwd'),log=str(log),env=env)
            result['arms'][arm]={'load_s':time.monotonic()-t0,'engine':engine.info,'args':engine_args(c)}
            assert engine.max_context==32768
            (a.out/'child-handle.json').write_text(json.dumps({'pid':engine.proc.pid,'arm':arm,'cleanup':'runner closes this exact engine in finally'}))
            print('READY',arm,round(result['arms'][arm]['load_s'],2),flush=True);save()
            def run(name,tag,rep=0,tune=None,limit=192):
                if subprocess.run(['systemctl','is-active','--quiet','strata-sc117']).returncode==0:
                    raise RuntimeError('Production service became active; aborting isolated test')
                f=fs[name];mon.label=f'{arm}/{tag}/{name}/{rep}/{tune}'
                offset=log.stat().st_size
                before=proc_snapshot();start=time.perf_counter();first=None;count=0
                # Keep generated text only transiently for a minimal output validity check.
                output=[]
                sampling={'temperature':0}
                if tune is not None:sampling['strata_tune']={'pcie_frac':tune}
                for token in engine.generate(f['ids'],limit,sampling,threading.Event()):
                    if token is not None:
                        if first is None:first=time.perf_counter()-start
                        count+=1;output.append(token)
                wall=time.perf_counter()-start
                last=dict(engine.last)
                assert last.get('reused',0)==0, 'Prefix reuse detected'
                text=tok.decode(output)
                if not text.strip() or count<8:raise RuntimeError('Empty/too-short synthetic output')
                with log.open() as lf:lf.seek(offset);lines=lf.read().splitlines()
                # Whitelist fixed native diagnostic lines; no general request/response logging.
                prefixes=('strata prefill timing:', 'strata prefill cpu:', 'strata decode timing:', 'strata decode GPU stages', 'strata serve: request', 'strata serve: expert tiers')
                row={'arm':arm,'tag':tag,'fixture':name,'repeat':rep,'pcie_frac':tune,'input_tokens':len(f['ids']),'emitted_tokens':count,'ttft_native_s':first,'wall_s':wall,'engine':last,'diagnostic_lines':[x for x in lines if x.startswith(prefixes)],'before':before,'after':proc_snapshot(),'output_nonempty':True,'replacement_chars':text.count('\ufffd')}
                result['rows'].append(row);save()
                print(json.dumps({k:row[k] for k in ('arm','tag','fixture','repeat','pcie_frac','ttft_native_s','wall_s','engine')}),flush=True)
            # Same warm-up and domain order in each arm; no OS cache flush.
            run('network_4096','warmup',limit=32)
            run('go_code_4096','warmup',limit=64)
            if arm not in ('baseline_after','sweep'):
                for rep in range(a.repeats):
                    names=list(fs)
                    if rep%2:names.reverse()
                    for name in names:run(name,'matrix',rep)
            elif arm=='baseline_after':
                for rep in range(a.repeats):
                    for name in ('network_4096','go_code_4096','zh_story_4096'):run(name,'matrix',rep)
            if arm in ('profile','sweep'):
                # Change only request-local PCIe offload share; counterbalanced order.
                for rep in range(2):
                    for name in ('network_4096','go_code_4096','zh_story_4096'):
                        for frac in (fractions if rep==0 else fractions[::-1]):
                            run(name,'pcie_sweep',rep,frac)
            engine.close();engine=None;mon.label=arm+'/closed';save()
        result['completed']=True
    finally:
        if engine is not None:engine.close()
        mon.close()
        result['production_config_unchanged']=a.config.read_bytes()==original_config
        result['production_finally_active']=subprocess.run(['systemctl','is-active','--quiet','strata-sc117']).returncode==0
        result['finished_at']=time.time();save()
        print('FINISHED',json.dumps({k:result.get(k) for k in ('completed','production_config_unchanged','production_finally_active')}),flush=True)

if __name__=='__main__':main()
