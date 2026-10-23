import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from leakage.ollama_model import OllamaModel


def test_native_local_tool_protocol():
    seen=[]
    def handler(req):
        import json
        payload=json.loads(req.content);seen.append(payload)
        return httpx.Response(200,json={'message':{'content':'','tool_calls':[{'function':{'name':'load_plan','arguments':{'plan_id':'P-12345'}}}]},'prompt_eval_count':12,'eval_count':5})
    m=OllamaModel(client=httpx.Client(transport=httpx.MockTransport(handler)))
    result=m.invoke([SystemMessage(content='Financial tools only'),HumanMessage(content='Check the plan'),AIMessage(content='',tool_calls=[{'name':'list_plans','args':{},'id':'earlier'}]),ToolMessage(content='[]',name='list_plans',tool_call_id='earlier')])
    assert result.tool_calls[0]['args']=={'plan_id':'P-12345'}
    assert result.usage_metadata['total_tokens']==17
    assert seen[0]['think'] is False and seen[0]['stream'] is False
    assert seen[0]['messages'][-1]['tool_name']=='list_plans'
    assert 'thinking' not in result.content


def test_local_inference_cannot_point_to_remote_host():
    for url in ['https://example.com','http://example.com','http://user:secret@localhost:11434']:
        with pytest.raises(ValueError):OllamaModel(base_url=url)


def test_local_provider_does_not_need_sponsor_credentials(root,monkeypatch):
    from leakage.agent import Agent
    monkeypatch.setenv('MODEL_PROVIDER','ollama')
    monkeypatch.delenv('SPONSOR_API_KEY',raising=False);monkeypatch.delenv('SPONSOR_MODEL',raising=False)
    a=Agent(root,mode='live');assert a.provider=='ollama' and isinstance(a.model,OllamaModel)


def test_cloud_model_cannot_be_used_in_local_mode():
    with pytest.raises(ValueError):OllamaModel(model="example:cloud")


def test_local_context_is_configurable_and_bounded(monkeypatch):
    monkeypatch.setenv("OLLAMA_NUM_CTX","131072")
    assert OllamaModel().context_length==131072
    monkeypatch.setenv("OLLAMA_NUM_CTX","999999")
    with pytest.raises(ValueError):OllamaModel()


def test_long_local_context_timeout_can_cover_prefill(monkeypatch):
    monkeypatch.setenv("OLLAMA_TIMEOUT_SECONDS","900")
    m=OllamaModel()
    assert m.request_timeout==900 and m.client.timeout.read==900
    monkeypatch.setenv("OLLAMA_TIMEOUT_SECONDS","99999")
    with pytest.raises(ValueError):OllamaModel()
