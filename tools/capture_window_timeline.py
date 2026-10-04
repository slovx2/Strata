"""Capture bounded synthetic windows using an isolated diagnostic engine."""
import argparse,json,os,sys,threading,time,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--source-root',type=Path,required=True);p.add_argument('--config',type=Path,required=True);p.add_argument('--exe',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
os.umask(0o077);sys.path[:0]=[str(a.source_root),str(a.source_root/'tools')]
from profile_song_pc import fixtures
from calibrate import with_arg
from serve.server import StrataEngine,engine_args,child_env
import strata_tokenizer as ST
assert str(a.out.resolve()).startswith('/mnt/data/')
assert not a.out.exists();a.out.mkdir(parents=True)
original=a.config.read_bytes();cfg=json.loads(original);cfg.pop('api_key',None)
assert subprocess.run(['systemctl','is-active','--quiet','strata-sc117']).returncode!=0
for proc in Path('/proc').iterdir():
 if not proc.name.isdigit():continue
 try:
  assert (proc/'exe').resolve() not in (Path(cfg['exe']).resolve(),a.exe.resolve()),'Engine already running'
 except (PermissionError,FileNotFoundError,OSError):pass
c=dict(cfg);c['exe']=str(a.exe);c['args']=with_arg(with_arg(c['args'],'--max-context','32768'),'--prompt-cache','0')
c['args']=with_arg(c['args'],'--pcie-frac','0.25')
assert not c.get('expert_profile_save') and '--expert-profile-save' not in c['args']
c['log']=str(a.out/'engine.log');env=child_env(c)
for k in list(env):
 if k.startswith('STRATA_') and any(s in k for s in ('DEBUG','DUMP','STATE_HASH','LOGPOS','DIAGNOSTIC','TRACE','TIMELINE')):env.pop(k,None)
env.update(STRATA_WINDOW_TIMELINE_PATH=str(a.out/'windows.jsonl'),STRATA_VERIFY_PROFILE='1',STRATA_DECODE_TIMING='1')
tp=Path(c['tokenizer']);vocab=json.loads((tp/'vocab.json').read_text());v=[None]*len(vocab)
for t,i in vocab.items():v[i]=t
tok=ST.Tokenizer(v,(tp/'merges.txt').read_text().split('\n'),json.loads((tp/'token_type.json').read_text()));fs=fixtures(tok)
engine=None;data={'context':32768,'source_revision':'7ebaacf','rows':[],'completed':False,'config_unchanged':False}
def save():
 t=a.out/'results.tmp';t.write_text(json.dumps(data,indent=2));t.replace(a.out/'results.json')
try:
 engine=StrataEngine.__new__(StrataEngine);engine.__init__(c['exe'],engine_args(c),cwd=c.get('cwd'),log=c['log'],env=env)
 data['engine']=engine.info;data['pid']=engine.proc.pid
 cases=[('network_4096',.25,'warmup',64),('go_code_4096',.25,'warmup',64)]
 for name,fracs in [('network_4096',(.25,.25)),('math_4096',(.25,.25)),('zh_story_4096',(.25,.25))]:
  cases.append((name,.25,'conditioning',64));cases.extend((name,f,'measure',192) for f in fracs)
 for name,frac,tag,limit in cases:
  assert subprocess.run(['systemctl','is-active','--quiet','strata-sc117']).returncode!=0
  start=time.monotonic();output=[]
  for token in engine.generate(fs[name]['ids'],limit,{'temperature':0,'strata_tune':{'pcie_frac':frac}},threading.Event()):
   if token is not None:output.append(token)
  assert len(output)>=8 and tok.decode(output).strip() and engine.last['reused']==0
  data['rows'].append({'request':len(data['rows'])+1,'fixture':name,'pcie_frac':frac,'tag':tag,'emitted':len(output),'output_nonempty':True,'wall_s':time.monotonic()-start,'engine':dict(engine.last)})
  save();print(json.dumps({k:data['rows'][-1][k] for k in ('request','fixture','pcie_frac','tag','emitted','wall_s')}),flush=True)
 data['completed']=True
finally:
 if engine:engine.close()
 data['config_unchanged']=a.config.read_bytes()==original;save()
print('FINISHED',data['completed'],data['config_unchanged'],flush=True)
