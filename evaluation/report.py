"""Export reproducible engineering metrics and a blank independent review worksheet."""
import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

parser=argparse.ArgumentParser()
parser.add_argument('results',type=Path)
parser.add_argument('--report',type=Path,default=Path('docs/EVALUATION.md'))
args=parser.parse_args()
rows=[json.loads(line) for line in args.results.read_text(encoding='utf-8').splitlines()]
summary=json.loads(args.results.with_suffix('.summary.json').read_text(encoding='utf-8'))
questions={q['id']:q for q in map(json.loads,Path(__file__).with_name('questions.jsonl').read_text(encoding='utf-8').splitlines())}
lines=['# 真实模型评测记录', '',
    f"2026-09-12；数据库 {summary['database']}；生成 {summary['generation_model']}；Embedding {summary['embedding_model']}。",
    f"保留集 40 题 × {summary['trials']} 次 × 4 基线，共 {len(rows)} 条；并发 {summary['concurrency']}。合成资料与参考标注由开发者编写，尚未独立人工复核。", '',
    '| 基线 | answered / partial / no_answer / retrieval_only / failed | 完成请求 P50 / P95 秒 |',
    '| --- | --- | --- |']
for baseline,metrics in summary['baselines'].items():
    group=[r for r in rows if r['baseline']==baseline]
    counts=Counter(r['status'] for r in group)
    labels=' / '.join(str(counts[s]) for s in ['answered','partial','no_answer','retrieval_only','failed'])
    done=metrics['completed']
    lines.append(f"| {baseline} | {labels} | {done['p50']:.2f} / {done['p95']:.2f} |")
lines+=['', 'B0 为关键词检索，B1 为固定文档 RAG，B2 为固定跨源流程，B3 为动态 Agent。answered 等状态由应用输出，不能当作人工判定的任务正确率。', '',
    '任务成功率、引用支持率、事实覆盖率保持未评定；不能仅凭状态分布断言动态 Agent 优于固定流程。当前证据最多六段，报告中的检索 recall@5 只用于检查文档召回，不能替代答案评审。', '',
    '## 待人工复核的样例', '']
for baseline in ['B1','B2','B3']:
    samples=[r for r in rows if r['baseline']==baseline and r['status'] in ['partial','failed']][:2]
    for row in samples:
        q=questions[row['question_id']]
        lines.append(f"- {baseline} / {row['question_id']} / 第 {row['trial']} 次：{q['question']} → {row['status']}。请核对证据是否充分、是否正确表达缺失信息。")
lines+=['', '## 复现与审阅', '',
    '`python evaluation/run.py --split heldout --trials 3 --concurrency 5 --baselines B0,B1,B2,B3 --output runtime/evaluation/heldout-postgres.jsonl`', '',
    '`python evaluation/report.py runtime/evaluation/heldout-postgres.jsonl`', '',
    f'原始结果 SHA-256：`{hashlib.sha256(args.results.read_bytes()).hexdigest()}`。', '',
    '原始结果与 `*.review.csv` 在 runtime/evaluation；包含每次答案、证据和实际返回的模型用量。审阅表留空的人工字段必须由独立审阅者填写，先核对参考答案，再逐事实检查引用。没有服务商 token 用量的本地 Embedding 记录保持 null；本报告未估算金额。', '',
    '局限：同一电脑同时运行数据库、CPU Embedding 与 Web 服务，未做独占负载隔离；这是一次合成保留集观测，尚非企业使用效果或完整产品验收结论。']
args.report.write_text('\n'.join(lines)+'\n',encoding='utf-8')
with args.results.with_suffix('.review.csv').open('w',encoding='utf-8-sig',newline='') as out:
    writer=csv.writer(out)
    writer.writerow(['question_id','baseline','trial','question','reference_draft','answer_and_evidence','human_task_success','human_supported_facts','human_verifiable_facts','human_cited_facts','reviewer','notes'])
    for r in rows:
        q=questions[r['question_id']]
        writer.writerow([r['question_id'],r['baseline'],r['trial'],q['question'],q['expected'],json.dumps(r.get('answer',r.get('evidence',[])),ensure_ascii=False),'','','','','',''])
print(f'Wrote {args.report} and independent review worksheet for {len(rows)} records.')
