"""Build a standalone interactive HTML report from parsed numeric results."""
import argparse
import json
from pathlib import Path

ap=argparse.ArgumentParser();ap.add_argument('parsed',type=Path);a=ap.parse_args()
data=json.loads(a.parsed.read_text())
page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Song PC · Strata 性能剖析</title>
<style>body{font:16px/1.65 system-ui,sans-serif;max-width:1150px;margin:32px auto;padding:0 24px;color:#172b44;background:#f5f8fc}h1{font-size:28px}h2{font-size:21px;margin-top:30px}section{background:white;border:1px solid #dce4ed;border-radius:12px;padding:20px;margin:18px 0}select{font:inherit;max-width:100%;padding:8px}table{border-collapse:collapse;width:100%;font-size:14px}td,th{border-bottom:1px solid #e3e8ef;text-align:left;padding:8px}small,.note{color:#57687d}.bar{display:flex;height:32px;border-radius:5px;overflow:hidden;background:#eee}.part{min-width:0}.legend{display:flex;flex-wrap:wrap;gap:8px 18px;margin-top:12px;font-size:14px}.dot{width:12px;height:12px;display:inline-block;margin-right:6px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));gap:14px}.metric{background:#f3f7fc;padding:12px;border-radius:7px}.metric b{display:block;font-size:23px}pre{overflow:auto;font-size:13px}button{font:inherit;padding:5px 12px;cursor:pointer}.warn{color:#9a5b00}a{color:#1463ac}</style>
<h1>Song PC · Strata 性能剖析</h1>
<p>RTX 4080 16GB · i5-13600KF · WSL 56GB 上限 · SC117 IQ3_S · 32K 测试上下文 · MTP 开启</p>
<p class="note">独立合成请求，关闭前缀复用，192-token 输出上限。此报告测量原生引擎，排除 HTTP 与网络开销。正式 256K 配置未改动。</p>
<section><h2>这次发现了什么</h2><p>共完成 88 个合成请求（含预热）。默认策略下，decode 详细探针中的专家到位等待占主模型 GPU 时间线约 56%–74%；4K prefill 的等待拷贝约占 48%。这些是暴露的等待，包含同步和调度，不能等同于纯 DMA 时间或 PCIe 已饱和。</p><p>优先尝试平衡 CPU 计算与 GPU 搬运：无 GPU 探针的第一轮相邻对照中，pcie_frac 从 0.55 调至 0.25，在命中率接近时提升约 27%–37%。第二轮受领域切换和缓存冷热影响明显，不能把两轮中位数的全部增益归因于参数。正式参数未调整，也尚未验证 256K 下的收益和输出质量。</p><div id="paired"></div><p class="note">本轮为常驻 RAM 专家模式，请求的专家文件读取计数为零。未进行单层 ID 排名；详细结论及复现方法见同附的中文报告。</p></section>
<section><h2>未开启详细计时的基准</h2><div id="baseline"></div><p class="note">数值为两轮中位数；缓存自适应和输出内容会变化，两次测量不能代表稳定的置信区间。</p></section>
<section><h2>逐请求查看计算与等待</h2><select id="select"></select><div id="detail"></div></section>
<section><h2>专家搬运比例对照</h2><div id="sweep"></div><p class="note">pcie_frac 是显存未命中专家中交给 GPU 的目标比例；0 仍然使用显存内专家。不是全部计算的 GPU 占比。正序＋倒序，每种设置每类输入两次。此对照保留自适应缓存和 MTP，输出路径不保证相同。</p></section>
<section><h2>阅读口径</h2><ul><li>GPU 阶段时间是流上的经过时间，包含等待，不是纯计算忙碌时间。</li><li>waitA：等待 CPU 发布计划；waitB：等待专家到位；waitCPU：等待 CPU 专家结果。</li><li>PCIe grp 是搬入后专家的 GPU 计算，不是总 DMA 时间；waitB 只反映未被隐藏的等待。</li><li>CPU 与 GPU 同时工作，其计时不能相加。GPU 利用率包含等待内核，不能单凭利用率判断算力是否饱和。</li><li>GDN/QSA 按层类型合计；本次没有逐个 layer ID 的排名。每窗口与每 token 是不同单位。</li><li>若 verifier 窗口数含短输入处理，下面不会把它冒充纯 decode 分解。</li><li>原生日志 hits/lookups 的分母漏掉 PCIe 分支，本报告用全部路由项重算。无窗口计数的基准使用末窗口截断界限估算；精确计数见详细请求。</li><li>详细计时会扰动速度，应以未开启详细计时的基准衡量吞吐。基准前后差异也含缓存与输出变化。</li></ul></section>
<script>const DATA=__DATA__;
const names={go_code:'Go 代码',network:'网络诊断',zh_story:'中文写作',math:'数学',en_science:'英文科学',mixed_structured:'多语言结构化'};
function label(s){for(const [k,v] of Object.entries(names))if(s.startsWith(k+'_'))return v+' / '+s.slice(k.length+1);return s}
const fmt=(v,n=1)=>Number(v).toFixed(n);
function table(head,rows){return '<table><thead><tr>'+head.map(h=>'<th>'+h+'</th>').join('')+'</tr></thead><tbody>'+rows.map(r=>'<tr>'+r.map(c=>'<td>'+c+'</td>').join('')+'</tr>').join('')+'</tbody></table>'}
let pairs=[];for(const f of ['network_4096','go_code_4096','zh_story_4096']){const rr=DATA.rows.filter(r=>r.arm==='sweep'&&r.tag==='pcie_sweep'&&r.repeat===0&&r.fixture===f),x=rr.find(r=>r.pcie_frac===0.55),y=rr.find(r=>r.pcie_frac===0.25);if(x&&y)pairs.push([label(f),fmt(x.decode_tps),fmt(y.decode_tps),'+'+fmt(100*(y.decode_tps/x.decode_tps-1))+'%',fmt(x.expert_hit_pct)+'% → '+fmt(y.expert_hit_pct)+'%'])}document.getElementById('paired').innerHTML=table(['第一轮 / 4K','默认0.55 tok/s','0.25 tok/s','变化','显存命中率变化'],pairs);
let base=[];for(const [k,s]of Object.entries(DATA.summary)){const [arm,tag,f]=k.split('/');if(arm==='baseline'&&tag==='matrix')base.push([label(f),fmt(s.prefill_tps,0),fmt(s.decode_tps),fmt(s.ttft_s??s.ttft_native_s,2),fmt(s.expert_hit_pct)+'%',fmt(s.draft_accept_pct)+'%'])}
document.getElementById('baseline').innerHTML=table(['输入 / tokens','Prefill tok/s','Decode tok/s','首 token 秒','命中率估计','草稿接受'],base);
let drift=[];for(const f of ['network_4096','go_code_4096','zh_story_4096']){let v=['baseline','profile','baseline_after'].map(arm=>DATA.summary[arm+'/matrix/'+f+'/None']);if(v.every(Boolean))drift.push([label(f),...v.map(s=>fmt(s.decode_tps))])}
document.getElementById('baseline').innerHTML+='<h3>前后复核 · Decode tok/s</h3>'+table(['输入','首次基线','开启GPU探针','关闭GPU探针后'],drift)+'<p class="note">复核只交错三类任务，与初始矩阵的缓存历史不同。跨启动波动明显，不能用本表精确隔离探针开销，也不能只挑最快数字。</p>';
const colors=['#2864d6','#12a895','#ec9b36','#dc6071','#8c62ba','#61788d','#a2b747'];
function bars(items){let total=items.reduce((s,x)=>s+x[1],0);if(!total)return '';return '<div class="bar">'+items.map((x,i)=>'<div class="part" style="width:'+x[1]/total*100+'%;background:'+colors[i%colors.length]+'" title="'+x[0]+': '+fmt(x[1],2)+' ms"></div>').join('')+'</div><div class="legend">'+items.map((x,i)=>'<span><i class="dot" style="background:'+colors[i%colors.length]+'"></i>'+x[0]+' '+fmt(x[1],2)+' ms ('+fmt(x[1]/total*100)+'%)</span>').join('')+'</div>'}
const rs=DATA.rows.filter(r=>r.arm==='profile'&&r.tag!=='warmup');const sel=document.getElementById('select');rs.forEach((r,i)=>{let o=document.createElement('option');o.value=i;o.textContent=label(r.fixture)+' · 第'+(r.repeat+1)+'轮 · '+r.tag+(r.pcie_frac===null?'':' · PCIe '+r.pcie_frac);sel.append(o)});
function render(){let r=rs[sel.value||0],h='<div class="grid">'+[['Prefill',fmt(r.prefill_tps,0)+' tok/s'],['Decode（计时开启）',fmt(r.decode_tps)+' tok/s'],['显存专家命中率',fmt(r.expert_hit_pct)+'%']].map(x=>'<p class="metric">'+x[0]+'<b>'+x[1]+'</b></p>').join('')+'</div>';
if(r.decode){let d=r.decode;h+='<p>每验证窗口 '+fmt(d.window_ms,2)+' ms，平均输出 '+fmt(d.tokens_per_window,2)+' token；主模型验证 '+fmt(d.verify_ms,2)+' ms，草稿 '+fmt(d.draft_ms,2)+' ms，提交/输出 '+fmt(d.commit_emit_ms,2)+' ms，剩余调度/缓存维护等 '+fmt(d.other_wall_ms,2)+' ms。</p>';h+='<p>每层每窗口：CPU 专家 '+fmt(d.cpu_experts_per_layer_window,2)+' 组，PCIe 专家 '+fmt(d.pcie_experts_per_layer_window,2)+' 组；这里按专家组计数，不能与按 token-expert 项计数的显存命中项直接算比例。</p>'}
if(r.gpu_stages&&r.gpu_decode_only){let all={};for(const s of Object.values(r.gpu_stages))for(const [k,v]of Object.entries(s))all[k]=(all[k]||0)+v;let chosen=['waitA','waitCPU','waitB','VRAM hits','PCIe grp'];let items=chosen.map(k=>[k,all[k]||0]);items.push(['其他 GPU 阶段',Object.entries(all).filter(([k])=>!chosen.includes(k)).reduce((s,[k,v])=>s+v,0)]);h+='<h3>主模型 GPU 时间线 · ms / 验证窗口</h3>'+bars(items);h+=table(['阶段','GDN 层合计 ms/window','QSA 层合计 ms/window'],Object.keys(all).map(k=>[k,fmt(r.gpu_stages.GDN?.[k]||0,2),fmt(r.gpu_stages.QSA?.[k]||0,2)]))}else if(r.gpu_stages){h+='<p class="warn">此请求 GPU 时间戳包含 prefill 窗口，未展示为纯 decode 分解。</p>'}
for(const p of r.prefill||[]){let items=Object.entries(p.stages).sort((a,b)=>b[1]-a[1]),top=items.slice(0,6);top.push(['其余',items.slice(6).reduce((s,x)=>s+x[1],0)]);h+='<h3>Prefill GPU 时间线 · '+p.tokens+' tokens</h3>'+bars(top)+'<p>GPU 时间线 '+p.timeline_ms+' ms；prefill 函数 wall '+p.wall_ms+' ms。主线程 staging 累计计数的本次增量 '+p.host_staging_delta_ms+' ms 与 GPU 活动重叠，不相加。</p>'}
h+='<details><summary>本条数字记录</summary><pre>'+JSON.stringify(r,null,2)+'</pre></details>';document.getElementById('detail').innerHTML=h}
sel.onchange=render;if(rs.length)render();
let sweep=[];for(const[k,s]of Object.entries(DATA.summary)){const[arm,tag,f,p]=k.split('/');if(tag==='pcie_sweep')sweep.push([arm,label(f),p,fmt(s.decode_tps),fmt(s.expert_hit_pct)+'%',fmt(s.draft_accept_pct)+'%'])}document.getElementById('sweep').innerHTML=table(['计时模式','输入','PCIe 比例','Decode tok/s','命中率估计','草稿接受'],sweep);
</script></html>'''
# Numeric metadata only, with safe embedding even if a future fixed label contains markup.
page=page.replace('__DATA__',json.dumps(data,ensure_ascii=False).replace('<','\\u003c'))
out=a.parsed.parent/'Song-PC-Strata-性能剖析.html';out.write_text(page)
print(out)
