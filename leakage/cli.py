"""Local maintenance and reproducible tool-level demo."""
import argparse
import json
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from .agent import Agent
from .actions import Actions
from .reconcile import reconcile

ROOT=Path(__file__).resolve().parents[1]

def demo(root=ROOT):
    agent=Agent(root,mode='offline');thread=agent.store.new_conversation()
    transcript=[]
    for text in ['Check P-12345','What currency is that plan in?','Propose a make-good invoice for October','Revise to 5000','yes','Check the other months']:
        state=agent.send(thread,text)
        transcript.append({'user':text,'assistant':state['messages'][-1].content})
    first=agent.store.read()['revision']
    # Re-open SQLite and canonical records to exercise actual restart recovery.
    resumed=Agent(root,mode='offline')
    resumed.send(thread,'roll back');resumed.send(thread,'yes');resumed.send(thread,'Check again')
    return {'mode':'offline-scripted, real tools and persistence','conversation':thread,'revision_after_apply':first,'revision_after_rollback':resumed.store.read()['revision'],'transcript':transcript,'final_findings':resumed.state(thread)['findings'],'receipts':Actions(resumed.store,thread).pending()}

def evaluate():
    expected={'P-12345':('missing_period','10000.00'),'P-PARTIAL':('underbilling','2000.00'),'P-OVER':('overbilling','-2000.00'),'P-CREDIT':('matched','0.00'),'P-DISCOUNT':('matched','0.00'),'P-FX':('matched','0.00'),'P-FX-MISSING':('unresolved',None),'P-OVERLAP':('unresolved',None),'P-AMEND':('matched','0.00')}
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory);shutil.copytree(ROOT/'data',root/'data')
        agent=Agent(root,mode='offline');start=time.monotonic();results=[]
        for plan,(kind,delta) in expected.items():
            findings=reconcile(agent.store.read(),plan)
            relevant=[f for f in findings if f['kind']==kind]
            results.append({'plan':plan,'passed':bool(relevant) and all(f['discrepancy']==delta for f in relevant),'evidence_covered':all(f['source_refs'] and f['calculation'] for f in findings)})
        report=demo(root)
        # Precision/recall restricted to the three known monetary anomaly plans.
        truth={(p,k) for p,(k,_) in expected.items() if k in ('missing_period','underbilling','overbilling')}
        # Use baseline originals; the demo ends rolled back.
        detected={(p,f['kind']) for p in expected for f in reconcile(agent.store.read(),p) if f['kind'] in ('missing_period','underbilling','overbilling')}
        return {'validation':'offline known-answer fixtures; no live model tested','cases':results,'precision':len(truth&detected)/len(detected) if detected else 1,'recall':len(truth&detected)/len(truth),'amount_accuracy':sum(r['passed'] for r in results)/len(results),'evidence_coverage':sum(r['evidence_covered'] for r in results)/len(results),'conversation_demo_passed':report['revision_after_apply']==1 and report['revision_after_rollback']==2,'latency_seconds':round(time.monotonic()-start,3),'model_calls':0,'model_tokens':0,'real_model_acceptance':'blocked: sponsor credentials not supplied'}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['demo','evaluate','reset']);parser.add_argument('--confirm');args=parser.parse_args()
    if args.command=='reset':
        if args.confirm!='RESET':parser.error('Stop Streamlit first, then supply --confirm RESET. Existing sandbox is archived.')
        sandbox=ROOT/'sandbox'
        if sandbox.exists():
            archive=ROOT/'archives'/('sandbox-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f'));archive.parent.mkdir(exist_ok=True);shutil.move(str(sandbox),str(archive));print('Archived existing sandbox to',archive)
        from .store import Store
        Store(ROOT);print('Reset complete; original data unchanged.')
    elif args.command=='evaluate':print(json.dumps(evaluate(),indent=2))
    else:
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);shutil.copytree(ROOT/'data',root/'data');print(json.dumps(demo(root),indent=2))

if __name__=='__main__':main()
