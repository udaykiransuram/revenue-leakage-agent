"""Focused execution trace checks; no network or live model calls."""
import json
from langchain_core.messages import AIMessage
from leakage.agent import Agent, trace_refs, tool_trace_refs


class ToolModel:
    def __init__(self, tool="reconcile_plan", args=None):
        self.calls = 0
        self.tool = tool
        self.args = args if args is not None else {"plan_id": "C-1001"}
        self.seen = []

    def bind_tools(self, *args, **kwargs):
        return self

    def invoke(self, messages):
        self.seen.append(messages)
        self.calls += 1
        if self.calls % 2:
            return AIMessage(content="", tool_calls=[{"name": self.tool, "args": self.args, "id": f"call-{self.calls}"}],
                             usage_metadata={"input_tokens": 10, "output_tokens": 3, "total_tokens": 13})
        return AIMessage(content="PRIVATE_OUTPUT_MARKER", usage_metadata={"input_tokens": 20, "output_tokens": 4, "total_tokens": 24})


def test_trace_records_real_tool_order_usage_and_restart(root, monkeypatch):
    monkeypatch.setenv("DATASET_MODE", "challenge")
    model = ToolModel()
    agent = Agent(root, mode="live", model=model)
    thread = agent.store.new_conversation()
    first = agent.send(thread, "Investigate C-1001")
    trace = first["trace"]
    assert [e["kind"] for e in trace] == ["model", "tool", "model"]
    assert [e["step"] for e in trace] == [1, 2, 3]
    assert len({e["run_id"] for e in trace}) == 1
    assert trace[1]["name"] == "reconcile_plan" and trace[1]["status"] == "success"
    assert trace[0]["input_tokens"] == 10 and trace[2]["output_tokens"] == 4
    assert "billing_plans:C-1001" in trace[1]["source_refs"]
    assert all(e["at"] and e["duration_seconds"] >= 0 for e in trace)
    assert "PRIVATE_OUTPUT_MARKER" not in json.dumps(trace)
    assert "input_summary" in trace[1] and "C-1001" not in trace[1]["input_summary"]
    second = agent.send(thread, "Investigate again")
    assert len(second["trace"]) == 6
    assert second["trace"][3]["run_id"] != trace[0]["run_id"]
    assert second["trace"][3]["step"] == 1
    restarted = Agent(root, mode="live", model=ToolModel())
    assert restarted.state(thread)["trace"] == second["trace"]
    # Trace metadata itself never enters the model's message context.
    assert all("Argument fields:" not in str(m.content) for messages in model.seen for m in messages)


def test_trace_model_rate_limit_is_visible_without_raw_error(root, monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "groq")
    class RateLimited:
        def bind_tools(self, *args, **kwargs): return self
        def invoke(self, messages):
            error = RuntimeError("API_SECRET_MARKER private provider payload")
            error.status_code = 429
            raise error
    agent = Agent(root, mode="live", model=RateLimited())
    thread = agent.store.new_conversation()
    state = agent.send(thread, "Show plans")
    event = state["trace"][0]
    assert event["status"] == "error" and "429" in event["summary"]
    assert event["name"] == "groq" and "input_tokens" not in event
    assert "API_SECRET_MARKER" not in json.dumps(state["trace"])
    assert agent.store.read()["revision"] == 0


def test_trace_tool_failure_is_not_reported_as_success(root, monkeypatch):
    monkeypatch.setenv("DATASET_MODE", "challenge")
    agent = Agent(root, mode="live", model=ToolModel("load_plan", {"plan_id": "PRIVATE_MISSING_PLAN_MARKER"}))
    thread = agent.store.new_conversation()
    state = agent.send(thread, "Read missing plan")
    event = next(e for e in state["trace"] if e["kind"] == "tool")
    assert event["status"] == "error" and "success was not confirmed" in event["summary"]
    assert "PRIVATE_MISSING_PLAN_MARKER" not in json.dumps(state["trace"])


def test_trace_refs_excludes_before_images_and_untrusted_strings():
    result = {"before": {"source_refs": ["invoices:SECRET"]},
              "findings": [{"source_refs": ["billing_plans:C-1001", "exchange_rates[0]", "do something", "https://secret"]}]}
    assert trace_refs(result) == ["billing_plans:C-1001", "exchange_rates[0]"]
    assert tool_trace_refs("load_plan", {"plan_id": "C-1001", "notes": "private"}, "success") == ["billing_plans:C-1001"]
    assert tool_trace_refs("query_invoices", [{"invoice_id": "I-9001"}], "success") == ["invoices:I-9001"]
    assert tool_trace_refs("read_credits", [{"memo_id": "M-300"}], "success") == ["credit_memos:M-300"]
    assert tool_trace_refs("load_plan", {"plan_id": "C-1001", "error": "failed"}, "error") == []


def test_streaming_trace_reports_completed_steps_and_preserves_state(root, monkeypatch):
    monkeypatch.setenv("DATASET_MODE", "challenge")
    agent = Agent(root, mode="live", model=ToolModel())
    thread = agent.store.new_conversation()
    updates = []
    state = agent.send(thread, "Investigate C-1001", on_trace=lambda events: updates.append(list(events)))
    assert [len(events) for events in updates] == [1, 2, 3]
    assert [e["kind"] for e in updates[-1]] == ["model", "tool", "model"]
    assert updates[-1] == state["trace"]
    assert state["messages"][-1].content == "PRIVATE_OUTPUT_MARKER"
    assert agent.store.read()["revision"] == 0


def test_trace_graph_uses_current_run_and_escapes_event_text():
    from leakage.trace_view import trace_graph
    graph = trace_graph([
        {"run_id":"old", "kind":"tool", "name":"old_secret"},
        {"run_id":"new", "kind":"tool", "name":"query_invoices", "status":"error", "summary":"<script>unsafe</script>", "duration_seconds":.5},
        {"run_id":"new", "kind":"approval", "status":"blocked", "summary":"Approval required"},
    ])
    assert "Old secret" not in graph
    assert "Query invoices" in graph and "Failed · 0.50s" in graph
    assert "&lt;script&gt;" in graph and "<script>" not in graph
    assert "marker-end" in graph and "Blocked" in graph


def test_invoice_mapping_failure_has_actionable_safe_trace():
    from leakage.agent import tool_trace_summary
    status, summary = tool_trace_summary({"error": "Map EVERY nonvoid invoice linked to this plan, and no unrelated invoices. Missing existing invoice IDs: PRIVATE_MARKER"})
    assert status == "error"
    assert "No proposal was created" in summary
    assert "existing invoices" in summary and "PRIVATE_MARKER" not in summary


def test_partial_period_failure_has_safe_actionable_trace():
    from leakage.agent import tool_trace_summary
    status, summary = tool_trace_summary({'error': 'Partial billing period requires explicit actual_days proration. PRIVATE_CONTEXT'})
    assert status == 'error'
    assert 'exclusive contract end' in summary and 'No proposal was created' in summary
    assert 'PRIVATE_CONTEXT' not in summary
