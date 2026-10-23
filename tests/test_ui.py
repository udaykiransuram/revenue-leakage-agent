"""Streamlit interactions run against isolated fixtures, never demo records."""
from pathlib import Path
from streamlit.testing.v1 import AppTest
from leakage.presentation import amount, period, diff_rows
from leakage.store import Store

def app(root, monkeypatch, management=True):
    monkeypatch.setenv("AGENT_MODE", "offline")
    monkeypatch.setenv("DATASET_MODE", "synthetic")
    source = (Path(__file__).resolve().parents[1] / "app.py").read_text().replace("ROOT = Path(__file__).resolve().parent", f"ROOT = Path({str(root)!r})")
    (root / "leakage").mkdir()
    at = AppTest.from_string(source, default_timeout=30).run()
    if at.toggle:
        at.toggle[0].set_value(management).run()
    return at

def widget(items, label):
    return next(x for x in items if x.label == label and not str(x.key or "").startswith("workspace-"))

def test_currency_and_period_presentation():
    assert amount("10000.00", "USD") == "USD 10,000.00"
    assert period(["2024-10-01", "2024-11-01"]) == "01 Oct 2024 – 31 Oct 2024"
    assert diff_rows({"amount":"1", "id":"x"},{"amount":"2", "id":"x"}) == [{"Field":"Amount", "Before":"1", "After":"2"}]

def test_record_filter_and_manual_edit_approval_rollback(root, monkeypatch):
    at = app(root, monkeypatch)
    assert not at.exception
    assert [t.label for t in at.tabs] == ["Investigation", "Contracts", "Records", "Approvals", "Activity & history"]
    widget(at.selectbox,"Record type").select("invoices").run()
    widget(at.selectbox,"Filter by plan").select("P-PARTIAL").run()
    assert not at.exception
    widget(at.multiselect,"Fields to change").select("amount").run()
    widget(at.text_input,"Amount").set_value("8500.00")
    widget(at.text_area,"Why should this record change?").set_value("Invoice corrected against source")
    widget(at.button,"Create edit proposal").click().run()
    assert not at.exception
    assert Store(root).read()["invoices"][2]["amount"] == "8000.00"
    assert widget(at.button,"Approve & apply this revision")
    assert at.session_state["active_view"] == "Approvals"
    widget(at.button,"Approve & apply this revision").click().run()
    assert not at.exception
    state = Store(root).read()
    assert state["invoices"][2]["amount"] == "8500.00"
    assert at.session_state["active_view"] == "Activity & history"
    receipt_count = len(state["receipts"])
    at.run()
    assert len(Store(root).read()["receipts"]) == receipt_count
    widget(at.button,"Prepare rollback").click().run()
    assert not at.exception
    assert Store(root).read()["invoices"][2]["amount"] == "8500.00"
    widget(at.button,"Approve & apply this revision").click().run()
    assert not at.exception
    assert Store(root).read()["invoices"][2]["amount"] == "8000.00"

def test_search_empty_and_exchange_rates_are_readable(root, monkeypatch):
    at = app(root, monkeypatch)
    widget(at.text_input,"Find a record").set_value("not-existing-record").run()
    assert any("No matching records" in i.value for i in at.info)
    widget(at.text_input,"Find a record").set_value("")
    widget(at.selectbox,"Record type").select("exchange_rates").run()
    assert not at.exception
    assert not any(b.label == "Create edit proposal" for b in at.button)


def test_plan_amendment_can_be_rejected_without_mutation(root, monkeypatch):
    at = app(root, monkeypatch)
    widget(at.text_input,"New recurring amount (USD)").set_value("11000.00")
    widget(at.text_area,"Commercial reason").set_value("Signed new terms")
    widget(at.button,"Preview amendment").click().run()
    assert not at.exception
    assert Store(root).read()["billing_plans"][0]["amount"] == "10000.00"
    widget(at.button,"Reject proposal").click().run()
    assert not at.exception
    state = Store(root).read()
    assert list(state["proposals"].values())[0]["status"] == "Rejected"
    assert not state["receipts"]


def test_invalid_manual_edit_shows_validation_without_writes(root, monkeypatch):
    at = app(root, monkeypatch)
    widget(at.selectbox,"Record type").select("invoices").run()
    widget(at.multiselect,"Fields to change").select("amount").run()
    widget(at.text_input,"Amount").set_value("not-money")
    widget(at.text_area,"Why should this record change?").set_value("Invalid request")
    widget(at.button,"Create edit proposal").click().run()
    assert not at.exception
    assert at.error
    assert not Store(root).read()["proposals"]


def test_legacy_json_is_rendered_as_records_without_history_change(root, monkeypatch):
    from leakage.agent import Agent
    from leakage.presentation import structured_content
    monkeypatch.setenv("AGENT_MODE", "offline")
    agent = Agent(root)
    thread = agent.store.new_conversation()
    original = 'Execution results:\n\n[{"id":"INV-OLD", "amount":"10000.00", "currency":"USD"}]'
    agent.record(thread,"Show records",original)
    at = app(root, monkeypatch)
    assert not at.exception
    assert any("Execution results:" == item.value for item in at.markdown)
    assert not any("INV-OLD" in item.value for item in at.markdown)
    assert any("INV-OLD" in str(table.value) for table in at.dataframe)
    assert Agent(root).state(thread)["messages"][-1].content == original
    assert structured_content("Nothing to change [today].") is None
    assert structured_content('```json\n[{"id":"x"}]\n```') == ("", [{"id":"x"}])


def test_new_investigation_keeps_selection_and_isolates_conversation(root, monkeypatch):
    at = app(root, monkeypatch)
    first = at.session_state["thread_id"]
    at.chat_input[0].set_value("Check P-12345").run()
    assert not at.exception
    widget(at.button,"＋ New investigation").click().run()
    second = at.session_state["thread_id"]
    assert first != second
    assert widget(at.selectbox,"Resume an investigation").value == second
    assert not any("Missing period" in m.value for m in at.markdown)
    at.run()
    assert at.session_state["thread_id"] == second
    assert widget(at.selectbox,"Resume an investigation").value == second
    widget(at.selectbox,"Resume an investigation").select(first).run()
    assert at.session_state["thread_id"] == first
    assert not at.exception
    from leakage.agent import Agent
    assert Agent(root).state(second).get("messages", []) == []
    assert Agent(root).state(first)["selected_plan"] == "P-12345"


def test_chat_submission_preserves_exact_text_across_reruns_and_threads(root, monkeypatch):
    from langchain_core.messages import HumanMessage
    from leakage.agent import Agent
    at = source_app(root, monkeypatch)
    first = at.session_state['thread_id']
    assert at.chat_input[0].key == 'chat_input_' + first
    questions = ['What is the revenue leakage for C-1001?', 'Check C-1007 instead, including unpaid invoices.']
    for question in questions:
        at.chat_input[0].set_value(question).run()
        assert not at.exception
        assert any(m.value == question for m in at.markdown)
    at.run()
    saved = Agent(root).state(first)['messages']
    assert [m.content for m in saved if isinstance(m, HumanMessage)] == questions
    widget(at.button, '＋ New investigation').click().run()
    second = at.session_state['thread_id']
    assert at.query_params['chat'] == second
    assert second != first
    assert at.chat_input[0].key == 'chat_input_' + second
    new_question = 'What currency is C-1001 in?'
    at.chat_input[0].set_value(new_question).run()
    assert not at.exception
    assert [m.content for m in Agent(root).state(second)['messages'] if isinstance(m, HumanMessage)] == [new_question]
    assert [m.content for m in Agent(root).state(first)['messages'] if isinstance(m, HumanMessage)] == questions


def test_resume_chat_appends_followup_and_preserves_other_chat(root, monkeypatch):
    from langchain_core.messages import HumanMessage
    from leakage.agent import Agent
    at = source_app(root, monkeypatch)
    first = at.session_state['thread_id']
    first_question = 'What is the revenue leakage for C-1001?'
    at.chat_input[0].set_value(first_question).run()
    widget(at.button, '＋ New investigation').click().run()
    second = at.session_state['thread_id']
    other_question = 'Check C-1007'
    at.chat_input[0].set_value(other_question).run()
    widget(at.selectbox, 'Resume an investigation').select(first).run()
    assert not at.exception
    assert at.session_state['thread_id'] == first
    followup = 'What about the missing September invoice for that same plan?'
    at.chat_input[0].set_value(followup).run()
    assert not at.exception
    assert at.session_state['thread_id'] == first
    saved = Agent(root)
    assert [m.content for m in saved.state(first)['messages'] if isinstance(m, HumanMessage)] == [first_question, followup]
    assert [m.content for m in saved.state(second)['messages'] if isinstance(m, HumanMessage)] == [other_question]
    widget(at.selectbox, 'Resume an investigation').select(second).run()
    assert at.session_state['thread_id'] == second
    widget(at.selectbox, 'Resume an investigation').select(first).run()
    assert at.session_state['thread_id'] == first
    assert not at.exception
    assert at.query_params['chat'] == first
    # A fresh browser session must resume the URL's chat, not the oldest chat.
    fresh = AppTest.from_file(at._script_path, default_timeout=30)
    fresh.query_params['chat'] = first
    fresh.run()
    assert not fresh.exception
    assert fresh.session_state['thread_id'] == first
    assert any(m.value == followup for m in fresh.markdown)


def test_navigation_persists_and_summary_is_compact(root, monkeypatch):
    at = app(root, monkeypatch)
    at.session_state["active_view"] = "Records"
    at.run()
    assert at.session_state["active_view"] == "Records"
    widget(at.text_input,"Find a record").set_value("P-12345").run()
    assert at.session_state["active_view"] == "Records"
    assert [t.label for t in at.tabs] == ["Investigation","Contracts","Records","Approvals","Activity & history"]
    assert any(m.value == "Revenue Leakage Agent" for m in at.title)
    assert not at.metric
    assert any("desk-summary" in m.value and "awaiting review" in m.value for m in at.markdown)
    assert not at.exception


def test_workspace_review_and_rollback_stay_with_conversation(root, monkeypatch):
    at = app(root, monkeypatch)
    at.chat_input[0].set_value("Check P-12345").run()
    assert not at.exception
    assert any(m.value == "Evidence & review" for m in at.subheader)
    assert any("USD 10,000.00" in str(t.value) for t in at.table)
    at.chat_input[0].set_value("Propose a make-good invoice for October").run()
    assert not at.exception
    assert at.session_state["active_view"] == "Investigation"
    next(b for b in at.button if str(b.key).startswith("workspace-approve-")).click().run()
    assert not at.exception
    assert at.session_state["active_view"] == "Investigation"
    assert len(Store(root).read()["receipts"]) == 1
    next(b for b in at.button if str(b.key).startswith("workspace-rollback-")).click().run()
    assert not at.exception
    assert at.session_state["active_view"] == "Investigation"
    next(b for b in at.button if str(b.key).startswith("workspace-approve-")).click().run()
    assert not at.exception
    assert len(Store(root).read()["receipts"]) == 2
    assert any(r.get("rolled_back") for r in Store(root).read()["receipts"].values())


def test_create_contract_review_and_history(root, monkeypatch):
    at=app(root,monkeypatch)
    widget(at.text_input,"Contract / plan ID").set_value("P-NEW")
    widget(at.text_input,"Customer ID").set_value("C-NEW")
    widget(at.text_input,"Product ID").set_value("service")
    widget(at.text_input,"Agreed recurring amount").set_value("1000.00")
    widget(at.text_area,"Reason for creating this contract").set_value("Approved sample agreement")
    widget(at.button,"Preview contract creation").click().run()
    assert not at.exception
    assert at.session_state["active_view"]=="Approvals"
    assert not any(p["id"]=="P-NEW" for p in Store(root).read()["billing_plans"])
    widget(at.button,"Approve & apply this revision").click().run()
    assert not at.exception
    assert any(p["id"]=="P-NEW" for p in Store(root).read()["billing_plans"])
    widget(at.selectbox,"Open contract").select("P-NEW").run()
    assert not at.exception
    assert any("Initial agreed terms" in str(t.value) for t in at.table)


def test_default_interview_workspace_is_chat_first(root, monkeypatch):
    at = app(root, monkeypatch, management=False)
    assert not at.exception
    assert not at.tabs
    assert at.chat_input
    assert not any(s.label == "Record type" for s in at.selectbox)
    assert not any(b.label == "Preview contract creation" for b in at.button)
    assert any("Agent conversation" == h.value for h in at.subheader)
    at.toggle[0].set_value(True).run()
    assert any(t.label == "Records" for t in at.tabs)
    assert not at.exception


def test_missing_groq_key_shows_configuration_guidance(root, monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "groq")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    source=(Path(__file__).resolve().parents[1]/"app.py").read_text().replace("ROOT = Path(__file__).resolve().parent",f"ROOT = Path({str(root)!r})").replace("from leakage.agent import Agent",'def Agent(root): raise ValueError("GROQ_API_KEY required")')
    at=AppTest.from_string(source,default_timeout=30).run()
    assert not at.exception
    assert any("Groq is selected" in item.value for item in at.info)
    assert not at.chat_input


def test_challenge_view_uses_original_counts_and_isolates_synthetic_threads(root, monkeypatch):
    import shutil
    monkeypatch.setenv("AGENT_MODE", "offline")
    monkeypatch.setenv("DATASET_MODE", "synthetic")
    prior=Store(root).new_conversation()
    monkeypatch.setenv("DATASET_MODE", "challenge")
    shutil.rmtree(root/"data")
    shutil.copytree(Path(__file__).resolve().parents[1]/"challenge_data",root/"data")
    (root/"leakage").mkdir()
    source=(Path(__file__).resolve().parents[1]/"app.py").read_text().replace("ROOT = Path(__file__).resolve().parent",f"ROOT = Path({str(root)!r})")
    at=AppTest.from_string(source,default_timeout=30).run()
    assert not at.exception
    assert [t.label for t in at.tabs] == ["Investigation","Contracts","Records","Approvals","Activity & history"]
    assert not at.toggle
    assert at.session_state["thread_id"] != prior
    assert prior not in widget(at.selectbox,"Resume an investigation").options
    assert any(b.label == "Check C-1001" for b in at.button)
    assert not any(b.label == "Check P-12345" for b in at.button)
    assert any('<strong>4</strong> plans' in m.value and '<strong>13</strong> invoices' in m.value for m in at.markdown)
    assert any(s.label == "Record type" for s in at.selectbox)
    widget(at.selectbox,"Record type").select("invoices").run()
    assert not at.exception
    assert not any(b.label == "Create edit proposal" for b in at.button)
    assert any("I-9001" in str(table.value) for table in at.dataframe)
    widget(at.selectbox,"Filter by plan").select("C-1001").run()
    assert not at.exception


def test_agent_trace_shows_latest_factual_steps_without_raw_payload(root, monkeypatch):
    monkeypatch.setenv("AGENT_MODE", "offline")
    monkeypatch.setenv("DATASET_MODE", "challenge")
    (root/"leakage").mkdir()
    events=[
        {"run_id":"old-run","step":1,"kind":"tool","name":"old_tool","status":"success","summary":"OLD TRACE SHOULD NOT SHOW"},
        {"run_id":"latest","step":1,"kind":"model","name":"opaque-model-name","status":"success","duration_seconds":0.35,"summary":"Model selected one tool.","input_tokens":1200,"output_tokens":33},
        {"run_id":"latest","step":2,"kind":"tool","name":"query_invoices","status":"success","duration_seconds":0.01,"summary":"Retrieved 9 source invoice records.","source_refs":["invoices:I-9001"],"raw_error":"SECRET EXCEPTION HEADER"},
        {"run_id":"latest","step":3,"kind":"model","name":"opaque-model-name","status":"error","duration_seconds":1.2,"summary":"The model request was rate limited."},
    ]
    source=(Path(__file__).resolve().parents[1]/"app.py").read_text().replace("ROOT = Path(__file__).resolve().parent",f"ROOT = Path({str(root)!r})").replace("conversation=agent.state(thread);",f"conversation={{**agent.state(thread), 'trace': {events!r}}};")
    at=AppTest.from_string(source,default_timeout=30).run()
    assert not at.exception
    assert any(e.label == "Agent trace" for e in at.expander)
    visible=" ".join(str(item.value) for collection in (at.markdown,at.caption) for item in collection)
    assert "Query invoices" in visible
    assert "Completed" in visible and "Failed" in visible
    assert "1,200 input tokens" in visible and "33 output tokens" in visible
    assert "invoices:I-9001" in visible
    assert "0.35 seconds" in visible
    assert "OLD TRACE SHOULD NOT SHOW" not in visible
    assert "SECRET EXCEPTION HEADER" not in visible
    assert "opaque-model-name" not in visible


def test_empty_agent_trace_is_truthful(root, monkeypatch):
    at=app(root,monkeypatch,management=False)
    assert any("Trace appears after a request runs." == item.value for item in at.caption)
    assert not at.exception


def source_app(root, monkeypatch, proposals=None):
    monkeypatch.setenv("AGENT_MODE", "offline")
    monkeypatch.setenv("DATASET_MODE", "challenge")
    (root/"leakage").mkdir(exist_ok=True)
    source=(Path(__file__).resolve().parents[1]/"app.py").read_text().replace("ROOT = Path(__file__).resolve().parent",f"ROOT = Path({str(root)!r})")
    if proposals is not None:
        source=source.replace("pending=actions.pending()",f"pending={proposals!r}",1)
    return AppTest.from_string(source,default_timeout=30).run()


def test_source_billing_context_is_distinct_from_financial_approval(root, monkeypatch):
    context={"period_amount":"8000.00","term_end":"2026-01-01","invoice_periods":{"I-9001":["2025-01-01","2025-02-01"]},"proration":"none","fx_date_policy":"invoice_issue_date","entitlement_scope":"all_plan_entitlements"}
    proposal={"kind":"billing_context","key":"PROP-CONTEXT:1","plan_id":"C-1001","status":"Awaiting approval","revision":1,"reason":"User confirmed the contract interpretation","currency":"USD","amount":None,"period":None,"preview":{"before":None,"after":context,"assumptions":["Confirm service allocation explicitly"]},"evidence":[]}
    at=source_app(root,monkeypatch,[proposal])
    assert not at.exception
    visible=" ".join(str(item.value) for collection in (at.markdown,at.caption,at.info) for item in collection)
    assert "Confirm billing context" in visible
    assert "creates no invoice or credit" in visible
    assert "separate proposal and approval" in visible
    assert any(b.label == "Approve & apply this revision" for b in at.button)
    assert not Store(root).read()["receipts"]
    assert any("Period amount" in str(t.value) and "8000.00" in str(t.value) for t in at.table)


def test_combined_correction_review_can_reject_or_apply_assumptions_together(root, monkeypatch):
    from datetime import date
    from leakage.actions import Actions
    from leakage.domain import add_months
    at = source_app(root, monkeypatch)
    store = Store(root)
    actions = Actions(store, at.session_state['thread_id'])
    original = store.read()['invoices']
    ctx = {'period_amount': '8000', 'term_end': '2026-01-01', 'reconcile_through': '2025-11-01',
           'proration': 'none', 'fx_date_policy': 'invoice_issue_date', 'entitlement_scope': 'all_plan_entitlements',
           'invoice_periods': {i['invoice_id']: [i['issue_date'][:7] + '-01', str(add_months(date.fromisoformat(i['issue_date'][:7] + '-01'), 1))]
                               for i in original if i['plan_id'] == 'C-1001'}}
    for choice in ('Reject proposal', 'Approve & apply this revision'):
        actions.draft('make_good', 'C-1001', '8000', 'Review proposed September recovery', billing_context=ctx)
        at.run()
        assert not at.exception
        assert any('confirms the assumptions below and applies' in w.value for w in at.warning)
        assert store.read()['invoices'] == original
        assert not store.read().get('billing_contexts')
        widget(at.button, choice).click().run()
        assert not at.exception
        if choice == 'Reject proposal':
            assert store.read()['invoices'] == original
            assert not store.read().get('billing_contexts')
    assert len(store.read()['invoices']) == len(original) + 1
    assert len(store.read()['receipts']) == 1


def test_source_native_amendment_and_invoice_previews_render_without_legacy_schedule(root, monkeypatch):
    proposals=[]
    for kind,preview in [("amendment",{"before":{"total_value":96000},"after":{"total_value":100000},"impact":"Prospective commercial terms only"}),("make_good",{"before":None,"after":{"invoice_id":"I-NEW","amount_invoiced":8000,"currency":"USD"}}),("credit",{"before":{"invoice_id":"I-9123","amount_invoiced":25000,"currency":"EUR"},"after":{"net_after_credit":"24000.00"}})]:
        proposals.append({"kind":kind,"key":"PROP-"+kind+":1","plan_id":"C-1001","status":"Awaiting approval","revision":1,"reason":"Verified source correction","currency":"USD","amount":"8000.00" if kind=="make_good" else "1000.00" if kind=="credit" else None,"period":None,"change_set":{"effective_date":"2025-10-01"},"preview":preview,"evidence":[]})
    at=source_app(root,monkeypatch,proposals)
    assert not at.exception
    assert any("Prospective commercial terms only" in str(m.value) for m in at.markdown)
    assert any("I-NEW" in str(t.value) for t in at.table)
    assert any("EUR 25,000.00" in str(m.value) for m in at.markdown)
    assert any("USD 24,000.00" in str(m.value) for m in at.markdown)


def test_source_records_show_active_sandbox_instead_of_original_files(root, monkeypatch):
    monkeypatch.setenv("DATASET_MODE", "challenge")
    store=Store(root)
    def append(state):
        state["invoices"].append({"invoice_id":"I-APPROVED-NEW","plan_id":"C-1001","customer_name":"ACME Corp","issue_date":"2025-11-01","amount_invoiced":8000,"currency":"USD","status":"unpaid","description":"Approved correction"})
    store.transaction(append)
    at=source_app(root,monkeypatch)
    assert not at.exception
    widget(at.selectbox,"Record type").select("invoices").run()
    assert not at.exception
    assert any("I-APPROVED-NEW" in str(t.value) for t in at.dataframe)
    assert "I-APPROVED-NEW" not in (root/"data"/"invoices.json").read_text()
