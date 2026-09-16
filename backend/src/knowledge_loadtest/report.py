import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from .common import write_json


def rows(out,name):
    path=Path(out)/name
    if not path.exists():return []
    result=[]
    for line in path.read_text(encoding='utf-8').splitlines():
        try:result.append(json.loads(line))
        except ValueError:pass  # Last in-flight row after abrupt process termination.
    return result


def percentile(values,p):
    values=sorted(values)
    return values[max(0,math.ceil(len(values)*p)-1)] if values else None


def summarize(out):
    out=Path(out);queries=rows(out,'queries.jsonl');http=rows(out,'http.jsonl');resources=rows(out,'resources.jsonl')
    environment=json.loads((out/'environment.json').read_text()) if (out/'environment.json').exists() else {}
    budget_runs={r.get('run_id') for r in rows(out,'gateway.jsonl') if r.get('budget_exhausted') and r.get('run_id')}
    claims={}
    for claim in rows(out,'claims.jsonl'):claims.setdefault(claim['run_id'],claim['claimed'])
    for q in queries:
        q['budget_limited']=q.get('run_id') in budget_runs
        if q.get('run_id') in claims:
            q['queue_seconds']=max(0,claims[q['run_id']]-q['sent'])
            q['execution_seconds']=max(0,q['finished']-claims[q['run_id']])
        else:q['queue_seconds']=q['execution_seconds']=None
    phases=[];windows=rows(out,'phases.jsonl')
    order=list(dict.fromkeys([r['phase'] for r in windows]+[r['phase'] for r in http+queries]))
    for phase in order:
        if phase in ('setup','persistence'):continue
        qs=[r for r in queries if r['phase']==phase]
        bounds=[r for r in windows if r['phase']==phase]
        hs=[r for r in http if r['phase']==phase and not r['expected_error'] and (not bounds or any(w['start']<=r['at']<=w['end'] for w in bounds))]
        reads=[r for r in hs if r['method']=='GET'];submit=[r for r in hs if r['method']=='POST' and r['endpoint'].endswith('/queries')]
        allseconds=[q['seconds'] for q in qs]
        spread=max(q['sent'] for q in qs)-min(q['sent'] for q in qs) if qs else None
        valid=sum(q['valid'] for q in qs);ratio=valid/len(qs) if qs else None
        row={'phase':phase,'queries':len(qs),'submitted':sum(q['submitted'] for q in qs),'completed':sum(q['completed'] for q in qs),'valid':valid,'valid_rate':ratio,
            'p50':percentile(allseconds,.5),'p95':percentile(allseconds,.95),'p99':percentile(allseconds,.99),'send_spread':spread,
            'read_p95':percentile([r['seconds'] for r in reads],.95),'read_success':sum(200<=r['status']<300 for r in reads)/len(reads) if reads else None,
            'submit_p95':percentile([r['seconds'] for r in submit],.95),'http_errors':sum(r['status']==0 or r['status']>=400 for r in hs),
            'submit_success':sum(r['status']==202 for r in submit)/len(submit) if submit else None,
            'sse_reconnects':sum(q['sse_reconnects'] for q in qs),
            'queue_p95':percentile([q['queue_seconds'] for q in qs if q.get('queue_seconds') is not None],.95),
            'within60':sum(q['valid'] and q['seconds']<=60 for q in qs)}
        row['budget_limited']=sum(q['budget_limited'] for q in qs)
        row['submit_verdict']='NOT_MEASURED' if not submit else 'PASS' if row['submit_success']>=.99 and row['submit_p95']<=1 else 'FAIL'
        if 'burst' in phase:
            row['verdict']='INVALID_BURST' if len(qs)!=100 or spread>1 else 'PASS' if row['within60']>=95 else 'FAIL'
        elif phase.startswith(('mixed','sustained','real_mixed')):
            row['verdict']='PASS' if qs and ratio>=.99 and row['p95']<=30 and row['read_success'] is not None and row['read_success']>=.99 and row['read_p95']<=1 and row['submit_success']>=.99 and row['submit_p95']<=1 else 'FAIL'
        elif phase.startswith('reads'):
            row['verdict']='PASS' if row['read_success'] is not None and row['read_success']>=.99 and row['read_p95']<=1 else 'FAIL'
        else:row['verdict']='OBSERVATION'
        if row['budget_limited']:row['verdict']='BUDGET_LIMITED'
        times=[r['at'] for r in hs]
        row['http_rps']=len(hs)/max(1,max(times)-min(times)) if times else 0
        phases.append(row)
    gateway=rows(out,'gateway.jsonl');real=[r for r in gateway if r['mode']=='real' and not r['budget_exhausted']]
    usage={}
    for r in real:
        for key,value in (r.get('usage') or {}).items():
            if isinstance(value,(int,float)):usage[key]=usage.get(key,0)+value
    result={'phases':phases,'real_calls':len(real),'usage':usage,'gateway_429':sum(r['status']==429 for r in gateway),'gateway_p95':percentile([r['seconds'] for r in gateway],.95),'recoveries':rows(out,'recovery.jsonl'),'stops':rows(out,'stops.jsonl')}
    endpoints=[];groups=defaultdict(list)
    for r in http:groups[(r['phase'],r['method'],r['endpoint'])].append(r)
    for (phase,method,path),group in sorted(groups.items()):
        endpoints.append({'phase':phase,'method':method,'endpoint':path,'requests':len(group),'success_rate':sum(200<=r['status']<300 for r in group)/len(group),
            'p50':percentile([r['seconds'] for r in group],.5),'p95':percentile([r['seconds'] for r in group],.95),'p99':percentile([r['seconds'] for r in group],.99)})
    result['recovery_pass']=all(r['queue_clear'] and r['seconds']<=120 and r['read_p95']<=1 for r in result['recoveries']) if result['recoveries'] else None
    result['max_validated_mixed_users']=max([int(r['phase'].rsplit('_',1)[1]) for r in phases if r['phase'].startswith(('mixed_','sustained_')) and r['verdict']=='PASS'] or [0]) or None
    logins=[r for r in http if r['phase']=='login_peak' and r['method']=='POST' and r['endpoint']=='/api/auth/login']
    result['login_peak_statuses']={str(status):sum(r['status']==status for r in logins) for status in {r['status'] for r in logins}}
    write_json(out/'summary.json',result)
    for name,data in [('queries',queries),('http',http),('phases',phases),('endpoints',endpoints)]:
        if not data:continue
        keys=sorted(set().union(*(r.keys() for r in data)))
        with (out/(name+'.csv')).open('w',newline='',encoding='utf-8-sig') as f:
            w=csv.DictWriter(f,fieldnames=keys);w.writeheader();w.writerows(data)
    lines=['# 100 人并发压测结果','', '隔离实例；客户端和服务端共享当前电脑。HTTP 202 不等于有效回答。P95 包含失败问答的观测耗时，未完成任务单列。',
        '', '| 场景 | 问答数 | 有效回答 | 总耗时 P95 秒 | 排队 P95 秒 | 读取 P95 秒 | 判定 |','|---|---:|---:|---:|---:|---:|---|']
    for r in phases:
        fmt=lambda x:'—' if x is None else f'{x:.3f}'
        lines.append(f"| {r['phase']} | {r['queries']} | {r['valid']} | {fmt(r['p95'])} | {fmt(r['queue_p95'])} | {fmt(r['read_p95'])} | {r['verdict']} |")
    lines+=['','## 结论边界',f'- 真实模型已转发 {len(real)} 次；用量统计见 summary.json。',
        '- 峰值表格的 PASS 仅判断 95/100 有效回答是否在 60 秒内完成；提交接口另按成功率 ≥99%、P95 ≤1 秒判断，见 summary.json 的 submit_verdict。',
        '- mock 是可控延迟和格式的模型替身；不是 DeepSeek 实际服务能力或语义质量测评。',
        '- smoke 只验证工具链和一轮峰值，不包含持续压力、阶梯和故障恢复，不能作为完整验收。',
        '- 发送跨度超过 1 秒或未发满 100 个问题的峰值轮次标记 INVALID_BURST，不能据此认定服务器峰值容量。',
        f"- 本轮问答槽位 {environment.get('worker_concurrency','未记录')}，总期限 {environment.get('run_timeout','未记录')} 秒，文档快路径 {environment.get('simple_document_fast_path','未记录')}。排队显著而读取稳定时提示问答执行容量受限。",
        '- 未执行的场景不推断通过；未测到的更高并发不推断容量。',
        f"- 最大已验证混合并发：{result['max_validated_mixed_users'] or '未完成有效阶梯验收'}；恢复检查：{result['recovery_pass']}。",
        f"- 停止原因：{result['stops'] or '无自动停止'}。",'', '![负载与延迟](performance.png)','![资源与队列](resources.png)']
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import pyplot as plt
    fig,axes=plt.subplots(2,1,figsize=(12,7),layout='constrained')
    labels=[r['phase'] for r in phases]
    axes[0].plot(labels,[r['p95'] if r['p95'] is not None else float('nan') for r in phases],marker='o',label='Query P95 seconds')
    axes[0].plot(labels,[r['queue_p95'] if r['queue_p95'] is not None else float('nan') for r in phases],marker='o',label='Queue P95 seconds');axes[0].legend()
    axes[1].bar(labels,[100*r['valid_rate'] if r['valid_rate'] is not None else float('nan') for r in phases]);axes[1].set_ylabel('Valid answers % (blank = no queries)')
    for ax in axes:ax.tick_params(axis='x',rotation=60)
    fig.savefig(out/'performance.png',dpi=130);plt.close(fig)
    fig,axes=plt.subplots(3,1,figsize=(12,8),layout='constrained')
    if resources:
        xs=[r['at']-resources[0]['at'] for r in resources]
        axes[0].plot(xs,[r['cpu'] for r in resources]);axes[0].set_ylabel('System CPU %')
        axes[1].plot(xs,[(r['memory_total']-r['memory_available'])/1024**3 for r in resources]);axes[1].set_ylabel('Used RAM GiB')
        axes[2].plot(xs,[r.get('states',{}).get('queued',0) for r in resources]);axes[2].set_ylabel('Queued queries');axes[2].set_xlabel('Elapsed seconds')
    fig.savefig(out/'resources.png',dpi=130);plt.close(fig)
    return result
