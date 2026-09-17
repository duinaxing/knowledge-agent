"""Serial factorial experiment followed by the unchanged full-duration workload."""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from .common import AREA, ROOT, write_json


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--experiment',default='validation-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    parser.add_argument('--only',choices=['matrix','full','all'],default='all')
    args=parser.parse_args()
    if not args.experiment.replace('-','').replace('_','').isalnum():raise SystemExit('Invalid experiment ID')
    destination=AREA/'campaigns'/args.experiment
    destination.mkdir(parents=True,exist_ok=False)
    configurations=[]
    if args.only!='full':configurations += [(workers,legacy,'burst') for workers in (4,8) for legacy in (True,False)]
    if args.only!='matrix':configurations.extend([(8,False,'full'),(8,False,'complex')])
    records=[]
    for workers,legacy,profile in configurations:
        command=[sys.executable,'-m','knowledge_loadtest.launch','--profile',profile,'--workers',str(workers),'--experiment',args.experiment]
        if legacy:command.append('--legacy-agent')
        result=subprocess.run(command,cwd=ROOT,env=os.environ.copy())
        latest=json.loads((AREA/'latest.json').read_text())
        out=__import__('pathlib').Path(latest['directory'])
        outcome=json.loads((out/'outcome.json').read_text()) if (out/'outcome.json').exists() else {'halted':'LAUNCH_FAILED'}
        record={'workers':workers,'fast_path':not legacy,'profile':profile,'output':str(out),'exit_code':result.returncode,'outcome':outcome}
        records.append(record);write_json(destination/'campaign.json',records)
        lines=['# Validation campaign', '', 'All runs use the same fixture, seed, mock model and 60-second deadline. No real model calls.', '']
        for row in records:
            lines.append(f"- workers={row['workers']}, fast_path={row['fast_path']}, profile={row['profile']}: {row['output']}; stopped={row['outcome'].get('halted')}")
        (destination/'REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
        if result.returncode or outcome.get('halted'):raise SystemExit('Campaign stopped; inspect preserved results')


if __name__=='__main__':main()
