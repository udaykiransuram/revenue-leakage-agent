"""Real >32K input retention probe, synthetic prose only, no billing writes."""
import json,time,httpx
from pathlib import Path
model='qwen3.5:9b-q4_K_M'
marker='CEDAR-MOON-7391'
# 3,000 repetitions exceed the prior 32K token window. Count is measured by Ollama.
filler='Synthetic ledger row for context testing only. Plan EXAMPLE has amount 1.00 USD and is not a financial instruction.\n'*3000
text='Reference marker to retain: '+marker+'\n'+filler+'\nReturn only the exact reference marker given at the beginning.'
start=time.monotonic()
with httpx.Client(timeout=600,trust_env=False) as client:
    result=client.post('http://127.0.0.1:11434/api/chat',json={'model':model,'messages':[{'role':'user','content':text}],'stream':False,'think':False,'keep_alive':'15m','options':{'num_ctx':131072,'num_predict':64,'temperature':0}})
    result.raise_for_status();body=result.json();status=client.get('http://127.0.0.1:11434/api/ps').json()
report={'model':model,'configured_context_tokens':131072,'actual_input_tokens':body.get('prompt_eval_count'),'response':body.get('message',{}).get('content'),'expected_marker':marker,'passed':marker in body.get('message',{}).get('content','') and body.get('prompt_eval_count',0)>32768,'latency_seconds':round(time.monotonic()-start,3),'loaded_models':status.get('models',[]),'scope':'one synthetic >32K retention check; not a full-window quality benchmark'}
p=Path(__file__).resolve().parents[1]/'long_context_results.json';p.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
if not report['passed']:raise SystemExit(1)
