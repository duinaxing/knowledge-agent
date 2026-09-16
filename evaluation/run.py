"""Run equal-resource baselines; export answers for human adjudication, never fake scores."""
import argparse
import json
import time
import statistics
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from knowledge_agent.db import transaction, Conversation, Run, engine
from knowledge_agent.security import identity, epoch, AppError
from knowledge_agent.runs import submit
from knowledge_agent.schemas import Query, Search
from knowledge_agent.retrieval import search
from knowledge_agent.agent import execute
from knowledge_agent.config import settings


def one(item, baseline, trial):
    start=time.monotonic()
    record={'question_id':item['id'],'baseline':baseline,'trial':trial,'category':item['category'],
            'reference_review':item['reference_review'],'human_task_success':None,
            'human_supported_facts':None,'human_verifiable_facts':None,'human_cited_facts':None}
    try:
        if baseline=='B0':
            with transaction() as db:
                evidence,_=search(db,identity(db,item['user_id']),Search(query=item['question'],project_id=item['project_id']),epoch(db),keyword_only=True)
            record.update(evidence=evidence,status='retrieval_only',usage=[])
        else:
            with transaction() as db:
                who=identity(db,item['user_id']);c=Conversation(owner_id=who.id,epoch=epoch(db));db.add(c);db.flush()
                r=submit(db,who,c.id,Query(message=item['question'],client_request_id=f'eval-{trial}-{baseline}'))
                r.state='running';r.generation=1;r.lease_until=time.time()+settings.run_timeout+1
                id_=r.id
            execute(id_,1,baseline=baseline)
            with transaction() as db:
                r=db.get(Run,id_)
                record.update(status=r.answer['status'],answer=r.answer,usage=r.usage,run_id=r.id,
                              model_calls=r.model_count,tool_calls=r.tool_count)
        evidence=record.get('evidence') or record.get('answer',{}).get('evidence',[])
        relevant=set(item['relevant_document_ids'])
        found=list(dict.fromkeys(e['document_id'] for e in evidence if e.get('document_id')))[:5]
        record['recall_at_5']=len(set(found)&relevant)/len(relevant) if relevant else None
    except Exception as exc:
        record.update(status='failed',error_code=exc.code if isinstance(exc,AppError) else type(exc).__name__)
    record['seconds']=time.monotonic()-start
    return record


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--split',choices=['dev','heldout'],default='dev')
    parser.add_argument('--trials',type=int,default=3)
    parser.add_argument('--limit',type=int,default=0)
    parser.add_argument('--baselines',default='B0,B1,B2,B3')
    parser.add_argument('--concurrency',type=int,default=1)
    parser.add_argument('--output',type=Path,default=Path('runtime/evaluation/results.jsonl'))
    args=parser.parse_args()
    if settings.model_mode!='real':raise SystemExit('Real-model evaluation refuses test mode')
    if not 1<=args.concurrency<=5 or not 1<=args.trials<=3:raise SystemExit('Invalid trial/concurrency range')
    items=[json.loads(l) for l in Path(__file__).with_name('questions.jsonl').read_text(encoding='utf-8').splitlines()]
    items=[q for q in items if q['split']==args.split]
    if args.limit:items=items[:args.limit]
    tasks=[(q,b,t) for b in args.baselines.split(',') for t in range(1,args.trials+1) for q in items]
    args.output.parent.mkdir(parents=True,exist_ok=True)
    rows=[]
    with args.output.open('w',encoding='utf-8') as out,ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        for r in pool.map(lambda task:one(*task),tasks):
            rows.append(r);out.write(json.dumps(r,ensure_ascii=False)+'\n');out.flush()
            print(f'{len(rows)}/{len(tasks)} {r["baseline"]} {r["status"]}',flush=True)
    summary={'database':engine.dialect.name,'generation_model':settings.model_name,'embedding_model':settings.embedding_model,
             'split':args.split,'trials':args.trials,'concurrency':args.concurrency,'samples':len(rows),
             'task_success_rate':None,'citation_support_rate':None,'reason':'Human review required','baselines':{}}
    for baseline in args.baselines.split(','):
        group=[r for r in rows if r['baseline']==baseline]
        summary['baselines'][baseline]={}
        for label in ['completed','failed']:
            times=sorted(r['seconds'] for r in group if (r['status']=='failed')==(label=='failed'))
            summary['baselines'][baseline][label]={'count':len(times),'p50':statistics.median(times) if times else None,
                      'p95':times[max(0,__import__('math').ceil(len(times)*.95)-1)] if times else None}
    args.output.with_suffix('.summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
