"""Import independent reviews; missing judgments never become successes."""
import argparse
import csv
import json
from pathlib import Path


def aggregate(rows):
    reviewed=[r for r in rows if r.get('reviewer','').strip() and r.get('reference_approved')=='1' and r.get('human_task_success') in ('0','1')]
    for row in reviewed:
        for key in ('human_cited_facts','human_supported_facts'):
            if row.get(key) and (not row[key].isdigit()):raise ValueError('Fact counts must be nonnegative integers')
        if row.get('human_cited_facts') and row.get('human_supported_facts') and int(row['human_supported_facts'])>int(row['human_cited_facts']):raise ValueError('Supported facts exceed cited facts')
    facts=[r for r in reviewed if r.get('human_cited_facts') and r.get('human_supported_facts')]
    cited=sum(int(r['human_cited_facts']) for r in facts)
    refusals=[r for r in reviewed if r['category'] in ('insufficient','permission') and r.get('human_refusal_correct') in ('0','1')]
    result={'total':len(rows),'reviewed':len(reviewed),'review_coverage':len(reviewed)/len(rows) if rows else 0,
            'task_success_rate':sum(int(r['human_task_success']) for r in reviewed)/len(reviewed) if reviewed else None,
            'citation_support_rate':sum(int(r['human_supported_facts']) for r in facts)/cited if cited else None,
            'refusal_correct_rate':sum(int(r['human_refusal_correct']) for r in refusals)/len(refusals) if refusals else None,
            'verdict':'PENDING' if len(reviewed)!=len(rows) or not rows else 'REVIEW_COMPLETE_CHECK_THRESHOLDS'}
    expected_facts=[r for r in rows if r['category'] not in ('general','insufficient','permission')]
    expected_refusals=[r for r in rows if r['category'] in ('insufficient','permission')]
    reviewed_fact_ids={r.get('id') for r in facts}
    if any(r.get('id') not in reviewed_fact_ids for r in expected_facts) or len(refusals)!=len(expected_refusals):result['verdict']='PENDING'
    if result['verdict']!='PENDING':
        result['verdict']='SEMANTIC_THRESHOLDS_PASS' if result['task_success_rate']>=.90 and (result['citation_support_rate'] or 0)>=.95 and (result['refusal_correct_rate'] or 0)>=.95 else 'FAIL'
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('csv',type=Path);args=parser.parse_args()
    with args.csv.open(encoding='utf-8-sig',newline='') as f:result=aggregate(list(csv.DictReader(f)))
    args.csv.with_suffix('.reviewed.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
