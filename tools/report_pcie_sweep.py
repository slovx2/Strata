"""Summarize the balanced synthetic PCIe sweep without response content."""
import argparse
import csv
import json
import math
from pathlib import Path
import statistics as stats

from summarize_song_pc_profile import parse


def summarize(data):
    rows=[parse(r) for r in data['rows'] if r['tag']=='pcie_sweep']
    groups={}
    for r in rows:groups.setdefault((r['fixture'],r['pcie_frac']),[]).append(r)
    values={k:stats.median(r['decode_tps'] for r in rs) for k,rs in groups.items()}
    domains=sorted({f for f,p in groups})
    fractions=sorted({p for f,p in groups})
    aggregate=[]
    for p in fractions:
        rs=[r for r in rows if r['pcie_frac']==p]
        if not all((f,p) in values and (f,0.55) in values for f in domains):continue
        ratios=[values[f,p]/values[f,0.55] for f in domains]
        aggregate.append({'pcie_frac':p,'n':len(rs),
            'pooled_decode_tps':sum(r['emitted_tokens'] for r in rs)/sum(r['decode_ms'] for r in rs)*1000,
            'relative_to_default_geomean':math.exp(stats.mean(math.log(x) for x in ratios)),
            'minimum_domain_gain_pct':100*(min(ratios)-1),
            'median_cpu_run_ms_per_window':stats.median(r['decode']['cpu_run_ms'] for r in rs),
            'median_hit_pct':stats.median(r['expert_hit_pct'] for r in rs)})
    best=max(aggregate,key=lambda a:a['relative_to_default_geomean'])['pcie_frac'] if aggregate else None
    return rows,groups,values,aggregate,best


def table(headers,rows):
    return '| '+' | '.join(headers)+' |\n|'+'|'.join(['---']*len(headers))+'|\n'+''.join('| '+' | '.join(map(str,r))+' |\n' for r in rows)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('results',type=Path);a=ap.parse_args()
    data=json.loads(a.results.read_text());rows,groups,values,aggregate,best=summarize(data)
    out=a.results.parent
    (out/'ranking.json').write_text(json.dumps({'best_measured':best,'aggregate':aggregate,'complete':data.get('completed',False)},indent=2))
    with (out/'measurements.csv').open('w') as f:
        cols=('fixture','repeat','pcie_frac','decode_tps','prefill_tps','expert_hit_pct','draft_accept_pct','decode_ms')
        w=csv.DictWriter(f,fieldnames=cols);w.writeheader();w.writerows({k:r[k] for k in cols} for r in rows)
    fractions=sorted({p for f,p in groups});domains=sorted({f for f,p in groups})
    body='''# Song PC PCIe 分工细调

RTX 4080 16GB / i5-13600KF / SC117 IQ3_S / INT8 KV / MTP 开启。32K 上下文容量、4K 合成输入、192-token 输出上限，关闭前缀复用和 thinking；无 GPU 时间戳探针，仅输出原生已有 host counters。排除 HTTP、网络与分词开销。

每轮覆盖六类输入。候选值采用平衡顺序：每个值在每个顺序位置出现相同次数，每种相邻先后组合出现相同次数。每个领域块前先用 0.25 做一次 64-token 条件化请求。仍保留自适应专家缓存，不能保证固定路由、固定输出或完全相同的缓存状态。本测试检查非空输出，未验证质量等价性。

'''
    sessions=data.get('engine_sessions',1)
    body+=f"纳入汇总 {len(data['rows'])} 个请求，含 {len(rows)} 个正式测量；完成状态：{data.get('completed',False)}。使用 {sessions} 个引擎会话；每个会话只加载一次模型，所有候选参数逐请求传入，不因参数切换重载。\n\n"
    if data.get('selection_note'):body+=data['selection_note']+'\n\n'
    body+='## 分领域 Decode 中位数（tok/s）\n\n'
    body+=table(['输入']+[str(p) for p in fractions],[[f]+[f'{values[f,p]:.2f}' if (f,p) in values else '—' for p in fractions] for f in domains])
    body+='\n## 综合比较\n\n'
    body+=table(['pcie_frac','测量数','合并吞吐 tok/s','对0.55的几何平均增益','最小领域增益','CPU ms/窗口中位数','命中率中位数'],[[r['pcie_frac'],r['n'],f"{r['pooled_decode_tps']:.2f}",f"{100*(r['relative_to_default_geomean']-1):+.1f}%",f"{r['minimum_domain_gain_pct']:+.1f}%",f"{r['median_cpu_run_ms_per_window']:.2f}",f"{r['median_hit_pct']:.2f}%"] for r in aggregate])
    body+=f'\n按六个领域等权、相对默认值的几何平均比值排名，当前最高候选为 **{best}**。这是有限候选、三轮测量的结果，不是全局最优证明。接近的候选应视为同一性能区间。合并吞吐采用总输出数/总 decode 时间，包含了不同输入的耗时权重。\n\n'
    body+='## 每项重复的范围\n\n'
    body+=table(['输入','比例','次数','最低 tok/s','中位数','最高 tok/s'],[[f,p,len(rs),f"{min(r['decode_tps'] for r in rs):.2f}",f"{values[f,p]:.2f}",f"{max(r['decode_tps'] for r in rs):.2f}"] for (f,p),rs in sorted(groups.items())])
    body+='\n结果不能直接外推到占满 256K 的长上下文，也不包含图片和多轮对话。`pcie_frac` 改变缺失专家的分工，不能从此表推算纯 CPU/GPU 算力或纯 DMA 带宽。\n'
    (out/'PCIe分工细调报告.md').write_text(body)
    print(json.dumps({'rows':len(rows),'best':best,'aggregate':aggregate},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
