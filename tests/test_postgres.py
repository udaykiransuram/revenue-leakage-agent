"""Real Postgres regression tests against disposable, uniquely scoped workspaces.

Set TEST_POSTGRES_URL to a dedicated test database, or install pgserver and set
USE_EMBEDDED_POSTGRES=1 to start an isolated Unix-socket-only database in /tmp.
Neither option uses DATABASE_URL from a developer's or production environment.
"""
import copy
import os
from pathlib import Path
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest


@pytest.fixture(scope='module')
def postgres_uri():
    if os.getenv('TEST_POSTGRES_URL'):
        yield os.environ['TEST_POSTGRES_URL']
        return
    if os.getenv('USE_EMBEDDED_POSTGRES') != '1':
        pytest.skip('Set TEST_POSTGRES_URL or USE_EMBEDDED_POSTGRES=1 for real database tests')
    pgserver = pytest.importorskip('pgserver')
    import fasteners
    with tempfile.TemporaryDirectory(prefix='revenue-pg-', dir='/tmp') as directory:
        runtime = Path(directory) / 'run'
        runtime.mkdir()
        cls = pgserver.PostgresServer
        original = cls.runtime_path, cls.lock_path, cls._lock
        cls.runtime_path = runtime
        cls.lock_path = runtime / '.lockfile'
        cls._lock = fasteners.InterProcessLock(cls.lock_path)
        data = Path(directory) / 'data'
        data.mkdir()
        server = None
        try:
            server = pgserver.get_server(data, cleanup_mode='stop')
            yield server.get_uri()
        finally:
            if server:
                server._cleanup()
            cls.runtime_path, cls.lock_path, cls._lock = original


@pytest.fixture
def hosted(root, postgres_uri, monkeypatch):
    pytest.importorskip('psycopg')
    from leakage.hosted import setup_database
    from leakage.postgres_store import PostgresStore
    monkeypatch.setenv('DATABASE_URL', postgres_uri)
    monkeypatch.setenv('DATABASE_URL_UNPOOLED', postgres_uri)
    monkeypatch.setenv('REVENUE_WORKSPACE', 'test-' + uuid.uuid4().hex)
    monkeypatch.setenv('PERSISTENCE_BACKEND', 'postgres')
    monkeypatch.setenv('DATASET_MODE', 'challenge')
    monkeypatch.setenv('MODEL_PROVIDER', 'groq')
    setup_database(root)
    store = PostgresStore(root)
    yield store
    # Delete only records belonging to this test's uniquely named workspace.
    from langgraph.checkpoint.postgres import PostgresSaver
    saver = PostgresSaver(store.pool)
    for thread in store.read()['conversations']:
        saver.delete_thread(thread)
    with store.pool.connection() as conn:
        conn.execute('DELETE FROM revenue_snapshots WHERE workspace = %s', (store.workspace,))
    store.close()


def test_database_transaction_rolls_back_and_never_creates_local_sandbox(hosted):
    before = hosted.read()
    def broken(state):
        state['invoices'][0]['amount_invoiced'] = 1
        state['audit'].append({'kind': 'not_committed'})
        raise ValueError('abort')
    with pytest.raises(ValueError, match='abort'):
        hosted.transaction(broken)
    assert hosted.read() == before
    assert not (hosted.root / 'sandbox').exists()


def test_independent_replica_pools_do_not_lose_concurrent_updates(hosted):
    from leakage.postgres_store import PostgresStore
    other = PostgresStore(hosted.root)
    def increment(index):
        store = hosted if index % 2 else other
        def change(state):
            state['test_counter'] = state.get('test_counter', 0) + 1
        store.transaction(change)
    try:
        with ThreadPoolExecutor(max_workers=6) as workers:
            list(workers.map(increment, range(30)))
        assert hosted.read()['test_counter'] == 30
        assert other.read()['test_counter'] == 30
    finally:
        other.close()


def test_repeated_setup_preserves_existing_state(hosted):
    from leakage.hosted import setup_database
    thread = hosted.new_conversation()
    before = hosted.read()
    setup_database(hosted.root)
    assert hosted.read() == before
    assert thread in hosted.read()['conversations']


def test_chat_checkpoints_survive_application_restart(hosted):
    from langchain_core.messages import AIMessage
    from leakage.agent import Agent
    from test_source_conversation import Model, call
    model = Model([call('reconcile_plan', {'plan_id': 'C-1007-A1'}), AIMessage(content='Linked net billed is USD 45,000; review the separate orphan.')])
    agent = Agent(hosted.root, mode='live', model=model)
    thread = agent.store.new_conversation()
    original = agent.send(thread, 'Investigate C-1007-A1 including its previous plan')
    agent.store.close()
    resumed = Agent(hosted.root, mode='live', model=Model([]))
    try:
        state = resumed.state(thread)
        assert state['messages'][-1].content == original['messages'][-1].content
        assert state['selected_plan'] == 'C-1007-A1'
        assert next(f for f in state['findings'] if f['kind'] == 'agreement_billing_summary')['net_billed'] == '45000.00'
        assert next(f for f in state['findings'] if f['kind'] == 'possible_orphan_invoice_match')['invoice_id'] == 'I-9202'
        assert state['trace']
        assert not (hosted.root / 'sandbox').exists()
    finally:
        resumed.store.close()


def test_approved_correction_receipt_is_idempotent_after_restart(hosted):
    from leakage.actions import Actions
    from leakage.postgres_store import PostgresStore
    from test_source_conversation import context
    thread = hosted.new_conversation()
    actions = Actions(hosted, thread)
    original = copy.deepcopy(hosted.read()['invoices'])
    proposal = actions.draft('make_good', 'C-1001', '8000', 'Review missing September charge',
                             billing_context=context(hosted.read()))
    with pytest.raises(ValueError, match='Explicit approval'):
        actions.apply(proposal['key'])
    assert hosted.read()['invoices'] == original
    hosted.displayed(thread, [proposal['key']])
    actions.approve([proposal['key']])
    receipt = actions.apply(proposal['key'])
    other = PostgresStore(hosted.root)
    try:
        replay = Actions(other, thread).apply(proposal['key'])
        assert replay['action_id'] == receipt['action_id']
        assert len(other.read()['invoices']) == len(original) + 1
        assert len(other.read()['receipts']) == 1
        assert any(event['kind'] == 'action_applied' for event in other.read()['audit'])
    finally:
        other.close()


def test_streamlit_renders_against_postgres_without_local_state(hosted, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setenv('AGENT_MODE', 'live')
    monkeypatch.delenv('GROQ_API_KEY', raising=False)
    source = (Path(__file__).resolve().parents[1] / 'app.py').read_text()
    source = source.replace('ROOT = Path(__file__).resolve().parent', f'ROOT = Path({str(hosted.root)!r})')
    (hosted.root / 'leakage').mkdir()
    app = AppTest.from_string(source, default_timeout=30).run()
    assert not app.exception
    assert [tab.label for tab in app.tabs] == ['Investigation', 'Contracts', 'Records', 'Approvals', 'Activity & history']
    assert hosted.read()['conversations']
    assert not (hosted.root / 'sandbox').exists()
