"""Local readiness only. Does not read or print API credentials or messages."""
import argparse,json,subprocess,sys,time,urllib.request
p=argparse.ArgumentParser();p.add_argument('--wait',action='store_true');a=p.parse_args()
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
start=time.monotonic();last_report=-20
while True:
 state=subprocess.run(['systemctl','is-active','strata-sc117'],capture_output=True,text=True).stdout.strip()
 if state!='active':
  print('service:',state or 'unknown');sys.exit(1 if a.wait else 0)
 try:
  with opener.open('http://127.0.0.1:8080/health',timeout=5) as r:h=json.load(r)
  if h.get('loaded'):
   print(json.dumps({k:h.get(k) for k in ('status','model','max_context','loaded','images','api_key','service')},ensure_ascii=False))
   print('Tailscale API: http://100.64.141.92:8080');sys.exit(0)
 except (OSError,ValueError):pass
 if not a.wait:print('service: active; model: loading');sys.exit(0)
 elapsed=time.monotonic()-start
 if elapsed>=600:print('等待模型就绪超时；服务仍可用 logs.sh 查看状态。');sys.exit(1)
 if elapsed-last_report>=20:print('模型加载中（%d 秒）…'%elapsed,flush=True);last_report=elapsed
 time.sleep(2)
