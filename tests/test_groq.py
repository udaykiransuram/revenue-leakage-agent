import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from leakage.agent import Agent


def test_groq_missing_key_does_not_fall_back(root,monkeypatch):
    monkeypatch.setenv('MODEL_PROVIDER','groq')
    monkeypatch.delenv('GROQ_API_KEY',raising=False)
    with pytest.raises(ValueError,match='Add GROQ_API_KEY'):
        Agent(root,mode='live')


def test_groq_configuration_uses_explicit_endpoint_and_no_retries(root,monkeypatch):
    import leakage.agent as module
    seen={}
    class Model:
        def __init__(self,**kwargs):seen.update(kwargs)
        def bind_tools(self,tools,**kwargs):return self
    monkeypatch.setattr(module,'ChatOpenAI',Model)
    monkeypatch.setenv('MODEL_PROVIDER','groq');monkeypatch.setenv('GROQ_API_KEY','test-only-not-real')
    a=Agent(root,mode='live')
    assert a.provider=='groq'
    assert seen['base_url']=='https://api.groq.com/openai/v1'
    assert seen['max_retries']==0
    assert seen['model']=='openai/gpt-oss-120b'


def test_groq_preserves_history_but_omits_unsupported_names(root,monkeypatch):
    class Model:
        def bind_tools(self,*args,**kwargs):return self
        def invoke(self,messages):
            assert all(m.name is None and 'name' not in m.additional_kwargs for m in messages)
            assert messages[-1].tool_call_id=='test-call'
            return AIMessage(content='Recorded result')
    monkeypatch.setenv('MODEL_PROVIDER','groq')
    a=Agent(root,mode='live',model=Model())
    thread=a.store.new_conversation()
    tool=ToolMessage(content='[]',name='list_plans',tool_call_id='test-call')
    result=a.model_node({'messages':[HumanMessage(content='List plans'),tool]},a.config(thread))
    assert result['messages'][0].content=='Recorded result'
    assert tool.name=='list_plans'


def test_groq_rate_limit_is_visible_without_fallback(root,monkeypatch):
    class Limited(Exception):status_code=429
    class Model:
        def bind_tools(self,*args,**kwargs):return self
        def invoke(self,messages):raise Limited()
    monkeypatch.setenv('MODEL_PROVIDER','groq')
    a=Agent(root,mode='live',model=Model());thread=a.store.new_conversation()
    result=a.model_node({'messages':[HumanMessage(content='Check billing')]},a.config(thread))
    assert 'request limit' in result['messages'][0].content
    assert 'no paid or local-model fallback' in result['messages'][0].content
    assert not a.store.read()['receipts']


class QueuedGroq:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
    def bind_tools(self, *args, **kwargs): return self
    def invoke(self, messages):
        self.requests.append(messages)
        response = self.responses.pop(0)
        if isinstance(response, Exception): raise response
        return response


def failure(status):
    error = RuntimeError('private exception payload must not appear in trace')
    error.status_code = status
    return error


def fallback_agent(root, monkeypatch, primary, backup):
    monkeypatch.setenv('MODEL_PROVIDER', 'groq')
    monkeypatch.setenv('DATASET_MODE', 'challenge')
    agent = Agent(root, mode='live', model=primary)
    agent.groq_models = [('primary-model', primary), ('backup-model', backup)]
    return agent


def test_fallback_preserves_history_and_continues_tools_without_replay(root, monkeypatch):
    primary = QueuedGroq([failure(429), AIMessage(content='Primary recovered on the next turn')])
    tool_call = AIMessage(content='', tool_calls=[{'name': 'load_plan', 'args': {'plan_id': 'C-1001'}, 'id': 'fallback-read'}])
    backup = QueuedGroq([tool_call, AIMessage(content='ACME is billed in USD')])
    agent = fallback_agent(root, monkeypatch, primary, backup)
    thread = agent.store.new_conversation()
    result = agent.send(thread, 'What currency is C-1001 in?')
    assert primary.requests[0] == backup.requests[0]
    assert len(primary.requests) == 1 and len(backup.requests) == 2
    assert isinstance(backup.requests[1][-1], ToolMessage)
    assert len([e for e in result['trace'] if e['kind'] == 'tool']) == 1
    steps = [e for e in result['trace'] if e['kind'] == 'model']
    assert [e['model_id'] for e in steps] == ['primary-model', 'backup-model', 'backup-model']
    assert [e['status'] for e in steps] == ['error', 'success', 'success']
    assert result['iterations'] == 3
    assert 'private exception' not in str(steps)
    assert not agent.store.read()['receipts']
    assert not agent.store.read()['proposals']
    agent.send(thread, 'What about the same plan?')
    assert len(primary.requests) == 2
    assert len(backup.requests) == 2


@pytest.mark.parametrize('status', [404, 408, 429, 500, 502, 503, 504])
def test_transient_or_unavailable_model_uses_backup(root, monkeypatch, status):
    primary = QueuedGroq([failure(status)])
    backup = QueuedGroq([AIMessage(content='Backup answered')])
    agent = fallback_agent(root, monkeypatch, primary, backup)
    thread = agent.store.new_conversation()
    result = agent.model_node({'messages': [HumanMessage(content='Check records')]}, agent.config(thread))
    assert result['messages'][0].content == 'Backup answered'
    assert len(backup.requests) == 1


@pytest.mark.parametrize('status', [400, 401, 403])
def test_bad_request_or_authentication_does_not_switch_models(root, monkeypatch, status):
    primary = QueuedGroq([failure(status)])
    backup = QueuedGroq([])
    agent = fallback_agent(root, monkeypatch, primary, backup)
    thread = agent.store.new_conversation()
    result = agent.model_node({'messages': [HumanMessage(content='Check records')]}, agent.config(thread))
    assert not backup.requests
    assert result['trace'][-1]['status'] == 'error'
    assert not agent.store.read()['receipts']


def test_exhausted_fallback_is_visible_without_financial_changes(root, monkeypatch):
    agent = fallback_agent(root, monkeypatch, QueuedGroq([failure(429)]), QueuedGroq([failure(429)]))
    thread = agent.store.new_conversation()
    result = agent.send(thread, 'Check C-1001')
    assert 'rate limit' in result['messages'][-1].content
    assert [e['status'] for e in result['trace']] == ['error', 'error']
    assert not agent.store.read()['receipts']
    assert not agent.store.read()['proposals']


def test_fallback_respects_total_eight_model_attempt_budget(root, monkeypatch):
    backup = QueuedGroq([])
    agent = fallback_agent(root, monkeypatch, QueuedGroq([failure(429)]), backup)
    thread = agent.store.new_conversation()
    result = agent.model_node({'messages': [HumanMessage(content='Check records')], 'iterations': 7}, agent.config(thread))
    assert not backup.requests
    assert result['iterations'] == 8


def test_configured_fallback_clients_have_tools_and_model_specific_options(root, monkeypatch):
    import leakage.agent as module
    clients = []
    class Configured:
        def __init__(self, **options): self.options = options; clients.append(self)
        def bind_tools(self, tools, **kwargs): self.tool_names = [t.name for t in tools]; return self
    monkeypatch.setattr(module, 'ChatOpenAI', Configured)
    monkeypatch.setenv('MODEL_PROVIDER', 'groq')
    monkeypatch.setenv('GROQ_API_KEY', 'test-not-real')
    monkeypatch.setenv('GROQ_MODEL', 'openai/gpt-oss-120b')
    monkeypatch.setenv('GROQ_FALLBACK_MODELS', 'openai/gpt-oss-20b, other/tool-model, openai/gpt-oss-20b')
    Agent(root, mode='live')
    assert [c.options['model'] for c in clients] == ['openai/gpt-oss-120b', 'openai/gpt-oss-20b', 'other/tool-model']
    assert all('propose_make_good_invoice' in c.tool_names for c in clients)
    assert all(c.options['max_retries'] == 0 for c in clients)
    assert clients[1].options['reasoning_effort'] == 'low'
    assert 'reasoning_effort' not in clients[2].options


def test_openrouter_and_local_backups_preserve_tools_and_provider_labels(root, monkeypatch):
    primary = QueuedGroq([failure(429)])
    groq_backup = QueuedGroq([failure(429)])
    router = QueuedGroq([failure(503)])
    tool = AIMessage(content='', tool_calls=[{'name': 'load_plan', 'args': {'plan_id': 'C-1001'}, 'id': 'local-read'}])
    local = QueuedGroq([tool, AIMessage(content='Local answer')])
    agent = fallback_agent(root, monkeypatch, primary, groq_backup)
    agent.groq_models += [('openrouter/free', router), ('qwen-local', local)]
    agent.openrouter_index = 2
    agent.ollama_index = 3
    thread = agent.store.new_conversation()
    result = agent.send(thread, 'What currency is C-1001 in?')
    assert result['messages'][-1].content == 'Local answer'
    steps = [e for e in result['trace'] if e['kind'] == 'model']
    assert [e['name'] for e in steps] == ['groq', 'groq', 'openrouter', 'ollama', 'ollama']
    assert result['iterations'] == 5
    assert len(primary.requests) == len(groq_backup.requests) == len(router.requests) == 1
    assert isinstance(local.requests[-1][-1], ToolMessage)
    assert local.requests[-1][-1].name == 'load_plan'
    assert not agent.store.read()['receipts']


def test_openrouter_uses_its_own_key_and_native_backup_is_bound(root, monkeypatch):
    import leakage.agent as module
    clients = []
    class Configured:
        def __init__(self, **options): self.options = options; clients.append(self)
        def bind_tools(self, tools, **kwargs): self.tool_names = [t.name for t in tools]; return self
    monkeypatch.setattr(module, 'ChatOpenAI', Configured)
    monkeypatch.setenv('MODEL_PROVIDER', 'groq')
    monkeypatch.setenv('GROQ_API_KEY', 'groq-test-key')
    monkeypatch.setenv('GROQ_FALLBACK_MODELS', 'openai/gpt-oss-20b')
    monkeypatch.setenv('OPENROUTER_BACKUP_ENABLED', 'true')
    monkeypatch.setenv('OPENROUTER_API_KEY', 'openrouter-test-key')
    monkeypatch.setenv('OPENROUTER_BACKUP_MODEL', 'openrouter/free')
    monkeypatch.setenv('OLLAMA_BACKUP_ENABLED', 'true')
    monkeypatch.setenv('OLLAMA_BACKUP_MODEL', 'qwen3:8b')
    agent = Agent(root, mode='live')
    assert clients[-1].options['base_url'] == 'https://openrouter.ai/api/v1'
    assert clients[-1].options['api_key'] == 'openrouter-test-key'
    assert clients[0].options['api_key'] == 'groq-test-key'
    assert 'reasoning_effort' not in clients[-1].options
    assert agent.openrouter_index == 2 and agent.ollama_index == 3
    local = agent.groq_models[-1][1]
    assert local.model == 'qwen3:8b'
    assert local.context_length == 32768
    assert any(t['function']['name'] == 'propose_make_good_invoice' for t in local.tools)


def test_missing_openrouter_key_skips_remote_backup(root, monkeypatch):
    import leakage.agent as module
    class Configured:
        def __init__(self, **kwargs): pass
        def bind_tools(self, *args, **kwargs): return self
    monkeypatch.setattr(module, 'ChatOpenAI', Configured)
    monkeypatch.setenv('MODEL_PROVIDER', 'groq')
    monkeypatch.setenv('GROQ_API_KEY', 'groq-test-key')
    monkeypatch.setenv('GROQ_FALLBACK_MODELS', '')
    monkeypatch.setenv('OPENROUTER_BACKUP_ENABLED', 'true')
    monkeypatch.delenv('OPENROUTER_API_KEY', raising=False)
    monkeypatch.setenv('OLLAMA_BACKUP_ENABLED', 'false')
    agent = Agent(root, mode='live')
    assert agent.openrouter_index is None
    assert len(agent.groq_models) == 1


@pytest.mark.parametrize('status', [401, 402, 403])
def test_openrouter_key_or_credit_failure_can_continue_locally(root, monkeypatch, status):
    primary = QueuedGroq([failure(429)])
    router = QueuedGroq([failure(status)])
    local = QueuedGroq([AIMessage(content='Local answer')])
    agent = fallback_agent(root, monkeypatch, primary, router)
    agent.openrouter_index = 1
    agent.ollama_index = 2
    agent.groq_models.append(('qwen-local', local))
    thread = agent.store.new_conversation()
    result = agent.send(thread, 'Check records')
    assert result['messages'][-1].content == 'Local answer'
    assert result['trace'][-1]['name'] == 'ollama'
    assert not agent.store.read()['receipts']
