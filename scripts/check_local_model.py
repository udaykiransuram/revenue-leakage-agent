"""Real local model acceptance on a temporary copy, never the interactive sandbox."""
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
import sys
APP=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(APP))
os.environ['MODEL_PROVIDER']='ollama'
os.environ.setdefault('OLLAMA_MODEL','qwen3.5:9b-q4_K_M')
os.environ.setdefault('OLLAMA_NUM_CTX','131072')
from leakage.agent import Agent
from leakage.actions import Actions
from leakage.reconcile import reconcile

report={'validation':'real local Ollama model with real tools, synthetic records','model':os.environ['OLLAMA_MODEL'],'context_tokens':int(os.getenv('OLLAMA_NUM_CTX','32768')),'cases':[],'limitations':['Synthetic input, not actual sponsor data','Does not validate sponsor model quality']}
started=time.monotonic()
try:
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory);shutil.copytree(APP/'data',root/'data')
        agent=Agent(root,mode='live');thread=agent.store.new_conversation()
        def send(text):
            before=len(agent.state(thread).get('activity',[]));start=time.monotonic();state=agent.send(thread,text)
            event={'request':text,'response':state['messages'][-1].content,'tools':[e['tool'] for e in state.get('activity',[])[before:]],'latency_seconds':round(time.monotonic()-start,3),'revision':agent.store.read()['revision']}
            report['cases'].append(event);print(json.dumps(event),flush=True);return event
        check=send('Investigate billing discrepancies for plan P-12345. Use the reconciliation tool to verify the expected and actual amounts for every service period.')
        assert 'reconcile_plan' in check['tools'],'No deterministic reconciliation tool was called'
        assert agent.store.read()['revision']==0
        currency=send('What currency is that plan in?')
        assert 'USD' in currency['response']
        assert 'reconcile_plan' not in currency['tools'],'Factual question unexpectedly forced full investigation'
        send('Please propose a make-good invoice for the missing October 2024 service period. Do not apply it yet.')
        pending=Actions(agent.store,thread).pending()
        assert len(pending)==1 and pending[0]['amount']=='10000.00','Expected one supported October recovery proposal'
        assert agent.store.read()['revision']==0
        # Application approval is exercised, not entrusted to a model interpretation.
        send('approve '+pending[0]['key'])
        assert agent.store.read()['revision']==1
        assert all(f['kind']=='matched' for f in reconcile(agent.store.read(),'P-12345'))
        resumed=Agent(root,mode='live');agent=resumed
        send('Check the same plan again. Did the correction resolve the missing period? Use fresh reconciliation results.')
        send('Please prepare a rollback proposal for the latest correction, and wait for approval.')
        pending=Actions(agent.store,thread).pending()
        assert len(pending)==1 and pending[0]['kind']=='rollback'
        send('approve '+pending[0]['key'])
        assert agent.store.read()['revision']==2
        assert any(f['kind']=='missing_period' for f in reconcile(agent.store.read(),'P-12345'))
        report['passed']=True
        report['model_usage']=agent.state(thread).get('usage',{})
except Exception as exc:
    report['passed']=False;report['failure']=f'{type(exc).__name__}: {exc}'
finally:
    report['total_latency_seconds']=round(time.monotonic()-started,3)
    (APP/os.getenv('LOCAL_MODEL_REPORT','local_model_results.json')).write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'passed':report['passed'],'failure':report.get('failure'),'latency':report['total_latency_seconds']}),flush=True)
if not report['passed']:sys.exit(1)
