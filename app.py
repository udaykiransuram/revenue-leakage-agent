from pathlib import Path
import re
import os
import hashlib
import json
from datetime import date, datetime
import streamlit as st
from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage
from leakage.agent import Agent
from leakage.actions import Actions
from leakage.trace_view import trace_graph, latest_steps
from leakage.presentation import label, amount, period, cell, record_rows, diff_rows, structured_content
def receipt_time(value):
    return datetime.fromisoformat(value).astimezone().strftime("%d %b %Y · %I:%M %p %Z")

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env", override=False)
st.set_page_config(page_title="Revenue desk · Leakage Agent", page_icon="◈", layout="wide", initial_sidebar_state="collapsed")
st.markdown("""<style>
:root{--desk-ink:#152b49;--desk-muted:#64748b;--desk-line:#dce4ee;--desk-accent:#087f74;}
[data-testid="stAppViewContainer"]{background:#f7f9fc;}
[data-testid="stHeader"]{background:rgba(247,249,252,.96);}
[data-testid="stSidebar"]{background:#fff;border-right:1px solid var(--desk-line);}
.block-container{max-width:1440px;padding:4rem 3rem 4rem;}
h1{font-size:2.25rem!important;letter-spacing:-.07rem;color:var(--desk-ink);font-weight:700!important;padding-bottom:.4rem!important;}
h2,h3{color:var(--desk-ink);letter-spacing:-.025rem;}
h3{font-size:1.16rem!important;}
p,li{line-height:1.65;}
[data-testid="stCaptionContainer"]{color:var(--desk-muted);}
.desk-brand{display:flex;align-items:center;justify-content:space-between;padding:0 0 20px;margin-bottom:22px;border-bottom:1px solid var(--desk-line);color:var(--desk-ink);font-size:1.3rem;font-weight:700;letter-spacing:-.035em;}
.desk-brand span{color:var(--desk-accent);font-weight:400;}
.desk-brand small{font-size:.78rem;font-weight:500;letter-spacing:0;color:var(--desk-muted);}
.desk-summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));background:#fff;border:1px solid var(--desk-line);border-radius:9px;padding:18px 0;margin:12px 0 18px;}
.desk-chip{display:flex;flex-direction:column-reverse;gap:4px;padding:0 28px;color:var(--desk-muted);font-size:.76rem;letter-spacing:.025em;border-right:1px solid #edf1f6;}
.desk-chip:last-child{border:0;}
.desk-chip strong{color:var(--desk-ink);font-size:1.8rem;line-height:1.3;font-weight:650;letter-spacing:-.04em;}
[data-baseweb="tab-list"]{gap:1.8rem;border-bottom:1px solid var(--desk-line);}
[data-baseweb="tab"]{padding:14px 4px;font-weight:600;color:#52647d;}
[data-baseweb="tab"][aria-selected="true"]{color:var(--desk-accent);}
[data-baseweb="tab-highlight"]{background:var(--desk-accent);height:3px;}
.workspace-step{display:flex;align-items:center;justify-content:space-between;gap:14px;color:var(--desk-muted);font-size:.8rem;padding:18px 8px 24px;}
.workspace-step span{display:flex;align-items:center;gap:10px;white-space:nowrap;}
.workspace-step b{display:inline-grid;place-items:center;width:30px;height:30px;border:1px solid #cbd5e1;border-radius:50%;font-weight:500;}
.workspace-step span:first-child{color:var(--desk-accent);font-weight:600;}
.workspace-step span:first-child b{background:var(--desk-accent);border-color:var(--desk-accent);color:white;}
.workspace-step i{height:1px;background:var(--desk-line);flex:1;}
.st-key-conversation-panel,.st-key-evidence-panel{background:#fff;border:1px solid var(--desk-line);border-radius:10px;padding:22px;}
.st-key-conversation-panel [data-testid="stChatMessage"]{background:#f3f6fa;border:0;border-radius:12px;padding:16px;min-width:0;}
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"]{overflow-wrap:anywhere;min-width:0;}
[data-testid="stChatMessageContent"]{min-width:0;overflow-x:auto;}
[data-testid="stChatMessageAvatarAssistant"]{background:var(--desk-accent);color:#fff;}
.st-key-conversation-panel .stButton>button{justify-content:space-between;min-height:46px;text-align:left;}
.stButton>button{border:1px solid #cbd5e1;border-radius:8px;color:var(--desk-ink);font-weight:500;transition:background .15s,border-color .15s;}
.stButton>button:hover{background:#f0faf8;border-color:var(--desk-accent);color:var(--desk-accent);}
.stButton>button[kind="primary"]{background:var(--desk-accent);border-color:var(--desk-accent);color:white;}
[data-testid="stChatInput"]{border:1px solid #cbd5e1;border-radius:9px;background:#fff;}
[data-testid="stChatInput"]:focus-within{border-color:var(--desk-accent);box-shadow:0 0 0 3px #087f7412;}
[data-testid="stExpander"]{border-color:var(--desk-line);border-radius:9px;background:#fff;}
[data-testid="stTable"], [data-testid="stDataFrame"]{overflow-x:auto;}
.desk-empty{display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:310px;text-align:center;padding:24px;}
.desk-empty svg{width:46px;height:46px;color:#71849e;box-sizing:content-box;background:#f3f6fa;padding:24px;border-radius:50%;margin-bottom:20px;}
.desk-empty h4{font-size:1.2rem;color:var(--desk-ink);margin:0 0 10px;letter-spacing:-.025em;}
.desk-empty p{max-width:350px;font-size:.9rem;color:var(--desk-muted);margin:0;}
[data-testid="stMetric"]{background:#fff;border:1px solid var(--desk-line);border-radius:9px;padding:16px;}
[data-testid="stMetricValue"]{font-size:1.3rem!important;white-space:normal;overflow-wrap:anywhere;}
@media(max-width:760px){.block-container{padding:3.5rem 1rem 3rem;}h1{font-size:1.8rem!important;}.desk-brand{font-size:1.1rem;margin-bottom:14px;padding-bottom:14px;}.desk-brand small{font-size:.7rem;}.desk-summary{grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;padding:16px 0;}.desk-chip{padding:0 20px;}.desk-chip:nth-child(2){border:0;}.desk-chip strong{font-size:1.5rem;}[data-baseweb="tab-list"]{gap:1rem;overflow-x:auto;}.workspace-step{display:grid;grid-template-columns:1fr 1fr;gap:12px;padding:16px 0;}.workspace-step i{display:none;}.workspace-step span{font-size:.72rem;}.workspace-step b{width:25px;height:25px;}.st-key-conversation-panel,.st-key-evidence-panel{padding:16px;}.desk-empty{min-height:240px;padding:20px 8px;}}

.st-key-conversation-panel [data-testid="stChatMessageContent"]{flex:1;min-width:0;width:0;max-width:100%;}
/* Conversation styling: natural page scrolling, no clipped message viewport. */
.st-key-conversation-panel{border-radius:18px;padding:28px 32px;box-shadow:0 8px 32px #152b4905;}
.st-key-conversation-panel h3{font-size:1.25rem!important;}
.st-key-conversation-panel [data-testid="stChatMessage"]{background:#fff;padding:20px 0;border-radius:0;margin:4px 0 16px;align-items:flex-start;gap:14px;}
.st-key-conversation-panel [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]){background:#edf7f5;border:1px solid #d6ece6;border-radius:18px 18px 4px 18px;padding:16px 20px;width:fit-content;max-width:85%;margin-left:auto;flex-direction:row-reverse;}
.st-key-conversation-panel [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) [data-testid="stChatMessageContent"]{width:auto;flex:0 1 auto;}
[data-testid="stChatMessageAvatarUser"]{background:#d5ebe5;color:#086b60;border-radius:50%;}
[data-testid="stChatMessageAvatarAssistant"]{background:#087f74;border-radius:10px;box-shadow:0 3px 8px #087f7418;}
.chat-author{font-size:.72rem;font-weight:650;color:#60768b;letter-spacing:.02em;margin:1px 0 9px;}
.st-key-conversation-panel [data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] p{font-size:.96rem;line-height:1.8;}
.st-key-conversation-panel [data-testid="stTable"]{border:1px solid #e3e9ef;border-radius:10px;margin:12px 0;}
.st-key-conversation-panel [data-testid="stTable"] th{background:#f6f8fb;color:#52647d;font-size:.8rem;}
.st-key-conversation-panel [data-testid="stTable"] td{font-size:.88rem;line-height:1.6;}
.st-key-conversation-panel [data-testid="stChatInput"]{background:#f8fafc;border:1px solid #cfdce5;border-radius:16px;box-shadow:0 4px 16px #152b4905;min-height:64px;padding:8px;margin-top:18px;}
.st-key-conversation-panel [data-testid="stChatInput"] textarea{font-size:.93rem;}
.st-key-conversation-panel [data-testid="stChatInputSubmitButton"]{background:#087f74;border-radius:10px;color:#fff;width:36px;height:36px;}
.st-key-conversation-panel [data-testid="stChatInputSubmitButton"]:disabled{background:#e5eeed;color:#8fa7a3;}
.chat-welcome{padding:22px 0 16px;max-width:620px;}
.chat-welcome h4{font-size:1.7rem;line-height:1.25;letter-spacing:-.045em;color:#152b49;margin:0 0 12px;}
.chat-welcome p{font-size:.96rem;color:#64748b;line-height:1.7;margin:0;}
.st-key-starter-prompts .stButton>button{background:#fff;border:1px solid #dce6ed;border-radius:12px;min-height:58px;padding:14px 18px;}
.st-key-starter-prompts .stButton>button:hover{background:#f0faf7;border-color:#087f74;transform:translateY(-1px);}
.chat-composer-hint{font-size:.72rem;color:#7c8b9a;margin-top:4px;text-align:center;}
@media(max-width:760px){.st-key-conversation-panel{padding:20px 16px;border-radius:14px;}.st-key-conversation-panel [data-testid="stChatMessage"]{gap:10px;}.st-key-conversation-panel [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]){max-width:94%;padding:12px 14px;}.chat-welcome h4{font-size:1.45rem;}.st-key-conversation-panel [data-testid="stChatInput"]{min-height:60px;}}
@media(prefers-reduced-motion:reduce){.st-key-starter-prompts .stButton>button{transition:none;transform:none;}}
</style>""",unsafe_allow_html=True)
@st.cache_resource
def engine(root_path, source_version, model_configuration):
    return Agent(Path(root_path),allow_unconfigured=True)
def readable_message(text):
    text=re.sub(r"\bF-[0-9a-f]+:\s*", "Finding: ", text)
    text=re.sub(r"\b[Pp]roposal PROP-[0-9a-f]+:\d+", "The correction", text)
    text=re.sub(r"\bPROP-[0-9a-f]+:\d+", "the correction", text)
    text=text.replace("approve The correction", "approve the correction")
    return text.replace("Inspect the preview below.","Expand Evidence & review below this conversation.").replace("as displayed below", "inside Evidence & review below")

def details(value):
    if isinstance(value,dict):
        rows=[{"Field":label(k),"Value":cell(v)} for k,v in value.items() if not isinstance(v,(dict,list))]
        if rows: st.table(rows)
        for k,v in value.items():
            if isinstance(v,(dict,list)) and v:
                with st.expander(label(k)): details(v)
    elif isinstance(value,list):
        if value and all(isinstance(v,dict) for v in value): st.dataframe(record_rows(value),hide_index=True,width="stretch")
        elif value:
            for v in value: st.write(cell(v))
        else: st.caption("No records.")
    else: st.write(cell(value))
try:
    agent=engine(str(ROOT), tuple((p.name,p.stat().st_mtime_ns) for p in sorted((ROOT/"leakage").glob("*.py"))), tuple(os.getenv(k, "") for k in ("AGENT_MODE", "MODEL_PROVIDER", "OLLAMA_MODEL", "OLLAMA_BASE_URL", "OLLAMA_NUM_CTX", "OLLAMA_TIMEOUT_SECONDS", "MODEL_NAME", "MODEL_BASE_URL", "GROQ_MODEL", "GROQ_FALLBACK_MODELS", "GROQ_MAX_OUTPUT_TOKENS", "OPENROUTER_BACKUP_ENABLED", "OPENROUTER_BACKUP_MODEL", "OPENROUTER_MAX_OUTPUT_TOKENS", "OLLAMA_BACKUP_ENABLED", "OLLAMA_BACKUP_MODEL", "OLLAMA_BACKUP_NUM_CTX", "OLLAMA_BACKUP_TIMEOUT_SECONDS", "DATASET_MODE")) + (hashlib.sha256(os.getenv("GROQ_API_KEY", "").encode()).hexdigest(), hashlib.sha256(os.getenv("OPENROUTER_API_KEY", "").encode()).hexdigest()))
except Exception as exc:
    if os.getenv("MODEL_PROVIDER") == "groq" and not os.getenv("GROQ_API_KEY"):
        st.title("Revenue Leakage Agent")
        st.info("Groq is selected. Add the application API key to the local environment to start the investigator.")
        st.caption("Configure GROQ_API_KEY and GROQ_MODEL in the application's .env file, then reload. Keep credentials out of the conversation.")
    else:
        st.error(str(exc))
    st.stop()
state=agent.store.read()
dataset_mode=os.getenv("DATASET_MODE", "synthetic")
challenge_mode=dataset_mode=="challenge"
def source_records(ledger):
    path=ROOT/"data"/(ledger+".json")
    if not path.exists(): path=ROOT/"challenge_data"/(ledger+".json")
    if not path.exists(): return []
    value=json.loads(path.read_text())
    return value if isinstance(value,list) else []
source_data=state  # Active sandbox records; original supplied JSON remains immutable.
ids=[key for key,value in state["conversations"].items() if value.get("dataset", "synthetic")==dataset_mode]
if "thread_id" not in st.session_state or st.session_state.thread_id not in ids:
    requested_thread=st.query_params.get("chat")
    st.session_state.thread_id=requested_thread if requested_thread in ids else next(iter(ids),None) or agent.store.new_conversation()
state=agent.store.read(); thread=st.session_state.thread_id
if st.query_params.get("chat") != thread:
    st.query_params["chat"] = thread
ids=[key for key,value in state["conversations"].items() if value.get("dataset", "synthetic")==dataset_mode]
# A pre-metadata store must never fall back to an unrelated dataset's conversation.
if thread not in ids: ids.append(thread)
conversation=agent.state(thread); actions=Actions(agent.store,thread); pending=actions.pending()
provider=getattr(agent,"provider",os.getenv("MODEL_PROVIDER","openai"))
model_label="Scripted demo" if agent.mode=="offline" else "Ollama local model" if provider=="ollama" else "Groq application model" if provider=="groq" else "Connected application model"
with st.sidebar:
    st.markdown("### ◈ Revenue desk"); st.caption("INVESTIGATE · REVIEW · RESOLVE"); st.divider()
    picker_key="conversation_picker_"+dataset_mode
    def resume_investigation():
        st.session_state.thread_id=st.session_state[picker_key]
        st.session_state["ui_next_view"]="Investigation"
    if st.session_state.get(picker_key) != thread:
        st.session_state[picker_key]=thread
    st.selectbox("Resume an investigation",ids,index=None,format_func=lambda x:state["conversations"][x]["title"]+" · "+receipt_time(state["conversations"][x]["created_at"])+" · "+x[:6],key=picker_key,on_change=resume_investigation)
    if st.button("＋ New investigation",width="stretch"): st.session_state.thread_id=agent.store.new_conversation(); st.session_state["ui_next_view"]="Investigation"; st.rerun()
    st.divider(); st.caption("APPLICATION MODEL"); st.write(model_label)
    if agent.mode=="offline": st.caption("Responses follow a demo script. Tools and approval controls are real.")
    elif provider=="groq":
        st.caption(os.getenv("GROQ_MODEL", "Configured Groq model"))
        if fallbacks := os.getenv("GROQ_FALLBACK_MODELS", "").strip(): st.caption("Fallback order · " + fallbacks)
        if os.getenv("OPENROUTER_BACKUP_ENABLED", "false").lower() == "true":
            st.caption("Hosted backup · OpenRouter / " + os.getenv("OPENROUTER_BACKUP_MODEL", "openrouter/free") if os.getenv("OPENROUTER_API_KEY") else "OpenRouter backup · add OPENROUTER_API_KEY to enable")
        if os.getenv("OLLAMA_BACKUP_ENABLED", "false").lower() == "true": st.caption("Final local backup · " + (os.getenv("OLLAMA_BACKUP_MODEL") or os.getenv("OLLAMA_MODEL", "qwen3:8b")))
        st.caption("Configured model fallback · financial calculations and approvals stay controlled by this app.")
    elif provider=="ollama":
        st.caption(os.getenv("OLLAMA_MODEL", "Local model"))
        context_tokens=int(os.getenv("OLLAMA_NUM_CTX", "32768"))
        st.caption(f"Context window · {context_tokens/1024:g}K tokens")
    st.caption("DATA ENVIRONMENT"); st.write("Challenge source records" if challenge_mode else "Local sandbox · revision "+str(state["revision"]))
    synthetic=not challenge_mode and all("SYNTHETIC" in p.get("notes", "") for p in state["billing_plans"]) if state["billing_plans"] else False
    st.caption(("Synthetic fixtures. " if synthetic else "Challenge records. ")+"Changes require a reviewed proposal; source files stay intact.")
st.markdown('<div class="desk-brand"><div>Revenue <span>desk</span></div><small>Billing operations</small></div>',unsafe_allow_html=True)
st.title("Revenue Leakage Agent")
st.caption("Investigate billing gaps, review corrections, and verify every change.")
if not getattr(agent,"model_configured",True):
    st.info("Groq is selected. Add GROQ_API_KEY to the local .env file, then reload. Record browsing remains available; no Ollama or scripted fallback is running.")
summary=[("plans",len(source_data["billing_plans"])),("invoices",len(source_data["invoices"])),("awaiting review",len(pending)),("actions",len(state["receipts"]))]
st.markdown('<div class="desk-summary">'+''.join(f'<span class="desk-chip"><strong>{value}</strong> {title}</span>' for title,value in summary)+'</div>',unsafe_allow_html=True)
def render_unresolved_drafts():
    drafts = [p for p in state["proposals"].values() if p["thread"] == thread and p["status"] == "Draft: needs details"]
    if drafts:
        st.subheader("Proposed fixes · details to resolve")
    for p in drafts:
        with st.container(border=True):
            st.markdown("#### " + ("Recover missing revenue" if p["kind"] == "make_good" else "Proposed credit"))
            st.caption(p["plan_id"] + " · " + p["status"])
            st.write(p["reason"])
            st.write("Suggested amount: **" + amount(p["amount"], p["currency"]) + "** · unverified")
            for issue in p.get("validation_issues", []):
                st.warning(issue)
            with st.expander("Proposed assumptions"):
                details(p["preview"].get("proposed_billing_assumptions") or {})
            st.caption("This draft has not changed any billing records. Clarify the unresolved details in chat to prepare a validated revision for approval.")

def render_approvals(prefix="", workspace=False):
    st.subheader("Review proposed changes"); st.caption("Approve an exact revision. Every applied change receives a receipt and a fresh verification.")
    if not pending: st.info("Nothing awaiting approval. Request a correction in chat or prepare a record edit.")
    for p in pending:
        with st.container(border=True):
            st.markdown("#### "+{"make_good":"Recover missing revenue","credit":"Issue a credit memo","amendment":"Amend commercial terms","rollback":"Roll back an action","manual_edit":"Edit a billing record","contract_create":"Create a contract","billing_context":"Confirm billing context"}.get(p["kind"],label(p["kind"])))
            st.caption(p["status"]+" · "+p["plan_id"]+" · revision "+str(p["revision"])); st.write(p["reason"])
            preview=p["preview"]
            for assumption in preview.get("derived_assumptions", []):
                st.info(assumption)
            if p.get("period"): st.caption("Service period: "+period(p["period"]))
            if p["kind"]=="billing_context":
                st.info("Confirm the pricing interpretation, service dates, FX date policy, and entitlement scope used for reconciliation. This approval saves billing context only; it creates no invoice or credit. Any financial correction requires a separate proposal and approval.")
                st.caption("Current interpretation")
                details(preview.get("before",{}))
                st.caption("Proposed billing context · facts and assumptions to confirm")
                details(preview.get("after",p.get("change_set",{})))
                for name,value in preview.items():
                    if name not in ("before","after"):
                        with st.expander(label(name)): details(value)
            elif p["kind"] in ("make_good","credit"):
                if preview.get("billing_assumptions"):
                    st.warning("Proposed correction based on assumptions. Approving this revision confirms the assumptions below and applies the displayed correction together. You can reject it without changing billing records.")
                    st.caption("Billing assumptions included in this proposal")
                    details({k: v for k, v in preview["billing_assumptions"].items() if k != "source_fingerprint"})
                if p["kind"]=="make_good" and isinstance(preview.get("before"),dict) and "period_net_billed" in preview["before"]:
                    values=[preview["before"]["period_net_billed"],p["amount"],preview["after"]["period_net_billed"]]; titles=["Currently billed","New invoice","Billed after correction"]
                elif p["kind"]=="credit" and isinstance(preview.get("after"),dict) and "net_after_credit" in preview["after"]:
                    values=[preview["before"].get("amount",preview["before"].get("amount_invoiced")),p["amount"],preview["after"]["net_after_credit"]]; titles=["Original invoice","New credit","Net after credit"]
                else:
                    values=[p.get("amount")]; titles=["Proposed correction"]
                    st.caption("Before correction"); details(preview.get("before",{}))
                    st.caption("After correction"); details(preview.get("after",{}))
                for col,title,v in zip(st.columns(len(values)),titles,values):
                    col.caption(title); col.write(amount(v,preview.get("before",{}).get("currency",p.get("currency","")) if title=="Original invoice" else p.get("currency","")))
            elif p["kind"]=="contract_create":
                st.caption("This billing plan will be the authoritative contract record. Approval creates no invoices.")
                details(preview.get("after",{}))
                if preview.get("billing_after"):
                    st.table([{"Service period":period([r["period_start"],r["period_end"]]),"Agreed amount":amount(r["expected"],p["currency"])} for r in preview["billing_after"]])
            elif p["kind"]=="amendment":
                effective=p.get("change_set",{}).get("effective_date")
                st.write(("Terms effective "+effective+". " if effective else "Commercial amendment. ")+"Existing invoices remain unchanged.")
                if "billing_before" in preview and "billing_after" in preview:
                    st.table([{"Service period":period([a["period_start"],a["period_end"]]),"Before":amount(b["expected"],p["currency"]),"After":amount(a["expected"],p["currency"])} for b,a in zip(preview["billing_before"],preview["billing_after"])])
                else:
                    st.caption("Current terms"); details(preview.get("before",{}))
                    st.caption("Proposed terms"); details(preview.get("after",{}))
                    if preview.get("impact"): st.write(cell(preview["impact"]))
            elif p["kind"]=="manual_edit":
                st.write(label(p.get("ledger","record"))+" · "+p.get("record_id","")); st.caption("Record currency: "+p["currency"]); st.table(diff_rows(preview["before"],preview["after"]))
            else:
                st.warning("Restore saved records from before action "+preview["target_action"]+". Audit history is retained.")
                for name in ["billing_plans","invoices","credit_memos","exchange_rates"]:
                    if preview["before"].get(name)!=preview["after"].get(name):
                        with st.expander(label(name)+" · rollback preview"):
                            st.caption("Current records"); details(preview["before"].get(name,[])); st.caption("Restored records"); details(preview["after"].get(name,[]))
            if p.get("evidence"):
                with st.expander("Supporting evidence"): details(p["evidence"])
            st.caption("Approval reference: "+p["key"])
            left,right=st.columns(2)
            if left.button("Approve & apply this revision",key=prefix+"approve-"+p["key"],type="primary",width="stretch"):
                try:
                    agent.store.displayed(thread,[v["key"] for v in actions.pending()]); agent.confirm(thread,[p["key"]])
                    st.session_state["ui_next_view"]="Investigation" if workspace else "Activity & history"; st.session_state["ui_notice"]="Approval processed. Inspect the receipt and refreshed investigation in this workspace."; st.rerun()
                except (ValueError,OSError) as exc: st.error(str(exc))
            if right.button("Reject proposal",key=prefix+"reject-"+p["key"],width="stretch"):
                actions.reject(p["key"]); agent.record(thread,"Reject "+p["key"],"Proposal rejected. Billing records are unchanged."); st.rerun()

def render_trace(events):
    """Show factual execution metadata only; model reasoning is never displayed."""
    with st.expander("Agent trace",expanded=False):
        if not events:
            st.caption("Trace appears after a request runs.")
            return
        latest_run=events[-1].get("run_id")
        steps=[event for event in events if event.get("run_id")==latest_run]
        st.caption("Latest request · factual model and tool activity")
        st.caption("Arrows show execution order. Each node is a recorded step; open the details below for evidence and token usage.")
        st.markdown(trace_graph(steps), unsafe_allow_html=True)
        for event in steps:
            kind=event.get("kind", "tool")
            name="Model response" if kind=="model" else "Approval check" if kind=="approval" else label(event.get("name", "Tool"))
            status=str(event.get("status", "recorded")).lower()
            status_label={"success":"Completed","completed":"Completed","ok":"Completed","error":"Failed","failed":"Failed","timeout":"Timed out","blocked":"Blocked","running":"Running"}.get(status,status.capitalize())
            st.markdown("**"+str(event.get("step", ""))+" · "+name+"** · "+status_label)
            if summary:=event.get("summary"):
                st.write(summary)
            metadata=[]
            if event.get("model_id"): metadata.append("Model: " + event["model_id"])
            duration=event.get("duration_seconds")
            if isinstance(duration,(int,float)): metadata.append(f"{duration:.2f} seconds")
            if isinstance(event.get("input_tokens"),int): metadata.append(f"{event['input_tokens']:,} input tokens")
            if isinstance(event.get("output_tokens"),int): metadata.append(f"{event['output_tokens']:,} output tokens")
            if metadata: st.caption(" · ".join(metadata))
            if event.get("source_refs"):
                st.caption("Evidence: "+" · ".join(str(ref) for ref in event["source_refs"]))

def send_with_trace(thread, text):
    with st.status("Running investigation…", expanded=True) as progress:
        st.caption("Graph updates when each model or tool step finishes.")
        graph = st.empty()
        observed = []
        def update(events):
            observed[:] = events
            graph.markdown(trace_graph(events), unsafe_allow_html=True)
        result = agent.send(thread, text, on_trace=update)
        failed = any(e.get("status") in ("error", "failed", "timeout") and not e.get("fallback_continues") for e in observed)
        progress.update(label="Request finished with an error" if failed else "Request finished", state="error" if failed else "complete", expanded=failed)
        return result

def render_history(prefix="", workspace=False):
    st.subheader("Execution history")
    receipts=[r for r in state["receipts"].values() if state["proposals"][r["proposal"]]["thread"]==thread]
    latest=Actions.latest(state)
    if not receipts: st.caption("No changes have been applied in this conversation.")
    for receipt in reversed(receipts):
        with st.container(border=True):
            st.markdown("**"+("Billing context confirmed" if receipt["kind"]=="billing_context" else label(receipt["kind"]))+"** · "+("Rolled back" if receipt.get("rolled_back") else receipt["status"]))
            if receipt["kind"]=="billing_context": st.caption("Billing interpretation saved. No invoice or credit was created by this action.")
            st.caption(receipt["plan_id"]+" · "+receipt_time(receipt["at"])+" · revision "+str(receipt["revision"])); st.caption("Receipt: "+receipt["action_id"])
            if latest and latest["action_id"]==receipt["action_id"] and st.button("Prepare rollback",key=prefix+"rollback-"+receipt["action_id"]):
                try:
                    actions.request_rollback(receipt["action_id"]); agent.store.displayed(thread,[p["key"] for p in actions.pending()])
                    st.session_state["ui_next_view"]="Investigation" if workspace else "Approvals"; st.session_state["ui_notice"]="Review the saved records that will be restored, then approve or reject the rollback."; st.rerun()
                except (ValueError,OSError) as exc: st.error(str(exc))
    with st.expander("Tool activity · factual inputs and results"):
        for event in conversation.get("activity",[]):
            st.markdown("**"+label(event.get("tool","Tool"))+"**"); st.caption(str(event.get("duration_seconds",""))+" seconds · revision "+str(event.get("revision","")))
            details(event.get("arguments",{}))
            with st.expander("Result"): details(event.get("result",{}))
    with st.expander("Audit trail"): details([e for e in state["audit"] if e.get("thread")==thread])
    with st.expander("All proposal outcomes"):
        rows=[{"Proposal":p["key"],"Type":label(p["kind"]),"Plan":p["plan_id"],"Status":p["status"],"Reason":p["reason"]} for p in state["proposals"].values() if p["thread"]==thread]
        if rows: st.dataframe(rows,hide_index=True,width="stretch")
        else: st.caption("No proposals yet.")

def render_contract_amendment(record,prefix):
    with st.expander("Propose a commercial amendment"):
        with st.form(prefix+"amend_"+record["id"]):
            effective=st.date_input("Effective date",value=date.fromisoformat(record["start_date"]))
            price=st.text_input("New recurring amount ("+record["currency"]+")",value=str(record["amount"]))
            discount=st.text_input("New discount (%)",value=str(record.get("discount_percent","0")))
            why=st.text_area("Commercial reason"); amend=st.form_submit_button("Preview amendment")
        if amend:
            try:
                proposal=actions.draft("amendment",record["id"],change_set={"effective_date":str(effective),"amount":price,"discount_percent":discount,"reason":why})
                agent.store.displayed(thread,[p["key"] for p in actions.pending()])
                agent.record(thread,"Propose commercial amendment for "+record["id"],"Commercial amendment prepared. Review the schedule impact in Approvals.",selected_plan=proposal["plan_id"],selected_period=None,findings=[]); st.session_state["ui_next_view"]="Approvals"; st.session_state["ui_notice"]="Review the schedule impact, then approve or reject the amendment."; st.rerun()
            except (ValueError,OSError) as exc: st.error(str(exc))

if notice:=st.session_state.pop("ui_notice",None): st.success(notice)
if next_view:=st.session_state.pop("ui_next_view",None): st.session_state["active_view"]=next_view
if challenge_mode:
    show_management=True
    st.caption("Active challenge sandbox · approved changes appear here; original supplied files remain unchanged and unresolved assumptions stay visible.")
else:
    show_management=st.toggle("Show record management",value=True,help="Optional record browsing and manual proposal forms. Investigation, evidence, approval, and rollback are available in chat.",key="show_management")
if show_management:
    chat_tab,contracts_tab,records_tab,approvals_tab,activity_tab=st.tabs(["Investigation","Contracts","Records","Approvals","Activity & history"],default="Investigation",key="active_view",on_change="rerun")
else:
    chat_tab=st.container()
with chat_tab:
    st.markdown('<div class="workspace-step"><span><b>1</b>Investigate</span><i></i><span><b>2</b>Inspect evidence</span><i></i><span><b>3</b>Review &amp; approve</span><i></i><span><b>4</b>Verify or roll back</span></div>',unsafe_allow_html=True)
    with st.container(key="conversation-panel"):
        st.subheader("Agent conversation"); st.caption("Ask, investigate, and resolve billing issues here.")
        events = conversation.get("trace", [])
        if events:
            latest_run = events[-1].get("run_id")
            model_steps = [e for e in events if e.get("run_id") == latest_run and e.get("kind") == "model"]
            if model_steps:
                elapsed = sum(e.get("duration_seconds", 0) for e in model_steps)
                failed = any(e.get("status") == "error" and not e.get("fallback_continues") for e in model_steps)
                tokens = sum(e.get("input_tokens", 0) + e.get("output_tokens", 0) for e in model_steps)
                request_provider = {"groq": "Groq", "openrouter": "OpenRouter", "sponsor": "Connected model", "ollama": "Ollama", "offline": "Scripted demo"}.get(model_steps[-1].get("name"), "Model")
                st.caption(f"Last request · {request_provider} · {len(model_steps)} model call(s) · {elapsed:.2f}s model time" + (f" · {tokens:,} tokens" if tokens else "") + (" · request failed" if failed else ""))
        with st.container(border=False):
            if not conversation.get("messages"):
                st.markdown('<div class="chat-welcome"><h4>Let’s find the gaps in your billing.</h4><p>Choose a billing plan or ask a question. I’ll help compare what was agreed with what was billed, then prepare corrections for your review.</p></div>',unsafe_allow_html=True)
                starts=[st.container(key="starter-prompts")]
                for text in (["Check "+p.get("plan_id",p.get("id","")) for p in source_data["billing_plans"][:2]] + ["List billing plans"]):
                    col=starts[0]
                    if col.button(text,width="stretch",disabled=not getattr(agent,"model_configured",True)):
                        with st.spinner("Checking records…"): send_with_trace(thread,text)
                        st.rerun()
            for message in conversation.get("messages",[]):
                if isinstance(message,(HumanMessage,AIMessage)) and message.content:
                    with st.chat_message("user" if isinstance(message,HumanMessage) else "assistant"):
                        st.markdown('<div class="chat-author">'+("You" if isinstance(message,HumanMessage) else "Revenue assistant")+'</div>',unsafe_allow_html=True)
                        if isinstance(message.content,str):
                            structured = structured_content(message.content) if isinstance(message, AIMessage) else None
                            if structured is not None:
                                prefix, payload = structured
                                if prefix: st.markdown(prefix)
                                details(payload)
                            else: st.markdown(readable_message(message.content) if isinstance(message,AIMessage) else message.content)
                        else:
                            for block in message.content:
                                if isinstance(block,dict) and block.get("text"): st.write(block["text"])
        if prompt:=st.chat_input("Ask about a plan, challenge a finding, or propose a correction",key="chat_input_"+thread,max_chars=12000,disabled=not getattr(agent,"model_configured",True)):
            try:
                with st.chat_message("user"):
                    st.markdown(prompt)
                with st.spinner("Checking the evidence…"): send_with_trace(thread,prompt)
                st.session_state["ui_next_view"]="Investigation"
                st.rerun()
            except (ValueError,OSError) as exc: st.error(str(exc))
        st.markdown('<div class="chat-composer-hint">Enter to send · Shift + Enter for a new line</div>',unsafe_allow_html=True)
    render_unresolved_drafts()
    if pending:
        render_approvals("workspace-", workspace=True)
    elif conversation.get("messages") and not any(p["thread"] == thread and p["status"] == "Draft: needs details" for p in state["proposals"].values()):
        st.caption("No proposed changes yet. Ask the agent to reconcile a plan and propose a correction; any missing billing details will need confirmation first.")
    render_trace(conversation.get("trace",[]))
    with st.expander("Evidence & review", expanded=False):
        findings=conversation.get("findings",[])
        st.subheader("Evidence & review")
        if findings:
            if conversation.get("observed_revision")!=state["revision"]: st.warning("Records changed. Ask for a fresh investigation before drawing financial conclusions.")
            st.caption(f"Investigated at revision {conversation.get('observed_revision')} · current revision {state['revision']}")
            rows=[{"Issue":label(f["kind"]),"Service period":period(f.get("period")),"Expected":amount(f.get("expected"),f.get("currency","")),"Billed":amount(f.get("actual"),f.get("currency","")),"Difference":amount(f.get("discrepancy"),f.get("currency","")),"Validation":f.get("validation","unresolved")} for f in findings]
            st.table(rows)
            for f in findings:
                with st.expander(label(f["kind"])+" · "+period(f.get("period"))):
                    st.markdown("**"+label(f["kind"])+"** · "+f["validation"].capitalize()); st.caption(period(f.get("period")))
                    metrics=st.columns(3)
                    for col,title,key in zip(metrics,["Expected","Actual billed","Difference"],["expected","actual","discrepancy"]):
                        col.caption(title); col.write(amount(f.get(key),f.get("currency","")))
                    with st.expander("How this was calculated"):
                        st.write(f.get("calculation","Calculation unavailable")); st.caption("Source records")
                        for ref in f.get("source_refs",[]): st.write("• "+ref)
                        if f.get("assumptions"): st.write("Assumptions: "+cell(f["assumptions"]))
                        st.caption("Finding reference: "+f["id"])
        if findings and conversation.get("observed_revision")!=state["revision"]:
            if st.button("Refresh investigation",key="workspace-refresh",type="primary"):
                try:
                    with st.spinner("Rechecking current records…"): send_with_trace(thread,"Recheck "+conversation.get("selected_plan", "the selected billing plan")+" against the current records.")
                    st.session_state["ui_next_view"]="Investigation"; st.rerun()
                except (ValueError,OSError) as exc: st.error(str(exc))
        if not findings:
            st.markdown('<div class="desk-empty"><svg viewBox="0 0 48 48" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M12 5h17l9 10v28H12z"/><path d="M29 5v11h9M19 25h12M19 32h12"/></svg><h4>Your evidence starts here</h4><p>Start an investigation to see reconciled amounts and source evidence here.</p></div>',unsafe_allow_html=True)
        if not pending and findings and conversation.get("observed_revision")==state["revision"]:
            issues=[f for f in findings if f.get("validation")=="validated" and f.get("supported_corrections") and f.get("discrepancy") not in (None,"0","0.00",0)]
            if issues and st.button("Prepare a correction",key="workspace-correction",type="primary",width="stretch"):
                try:
                    with st.spinner("Preparing a correction for review…"): send_with_trace(thread,"Propose a correction for the validated billing discrepancies in this investigation. Do not execute it.")
                    st.session_state["ui_next_view"]="Investigation"; st.rerun()
                except (ValueError,OSError) as exc: st.error(str(exc))
        selected_plan=conversation.get("selected_plan")
        if selected_plan:
            with st.expander("Source records · "+selected_plan):
                st.caption("Active contract / billing plan")
                details([p for p in source_data["billing_plans"] if p.get("plan_id",p.get("id"))==selected_plan])
                details([i for i in source_data["invoices"] if i.get("plan_id")==selected_plan])
                if challenge_mode:
                    with st.expander("Original supplied evidence · unchanged"):
                        details([p for p in source_records("billing_plans") if p.get("plan_id",p.get("id"))==selected_plan])
                        details([i for i in source_records("invoices") if i.get("plan_id")==selected_plan])
        with st.expander("Agent activity & audit history",expanded=False):
            render_history("workspace-", workspace=True)
if not show_management:
    if agent.store.projection_error: st.warning(agent.store.projection_error)
    st.stop()
with records_tab:
    st.subheader("Billing records"); st.caption("Browse the sandbox. Editing prepares a proposal; approval applies the change.")
    left,right=st.columns([1,2])
    ledger=left.selectbox("Record type",["billing_plans","invoices","credit_memos","exchange_rates"],format_func=label)
    query=right.text_input("Find a record",placeholder="Search record, customer, plan, or currency")
    plan_filter=st.selectbox("Filter by plan",["All plans"]+[p.get("plan_id",p.get("id")) for p in source_data["billing_plans"]])
    records=source_data[ledger]
    if plan_filter!="All plans" and ledger!="exchange_rates":
        invoice_ids={i.get("invoice_id",i.get("id")) for i in source_data["invoices"] if i.get("plan_id")==plan_filter}
        records=[r for r in records if (r.get("plan_id",r.get("id"))==plan_filter if ledger=="billing_plans" else r.get("plan_id")==plan_filter if ledger=="invoices" else r.get("invoice_id") in invoice_ids)]
    if query: records=[r for r in records if query.casefold() in cell(r).casefold()]
    st.caption(f"{len(records)} record(s) · amounts retain their original currency")
    if records:
        st.dataframe(record_rows(records),hide_index=True,width="stretch")
        if challenge_mode:
            idx=st.selectbox("Open source record",list(range(len(records))),format_func=lambda i:str(records[i].get("plan_id") if ledger=="billing_plans" else records[i].get("invoice_id") if ledger=="invoices" else records[i].get("memo_id",records[i].get("id", "Rate")))+" · row "+str(i+1))
            with st.container(border=True): details(records[idx])
            st.info("These are active sandbox records in the supplied schema. Use chat to request a record correction, credit, invoice, or commercial amendment. The agent confirms required billing context and displays each exact change for approval before applying it.")
        if not challenge_mode and ledger!="exchange_rates":
            idx=st.selectbox("Open record",list(range(len(records))),format_func=lambda i:records[i]["id"]+" · row "+str(i+1))
            record=records[idx]
            with st.container(border=True): st.markdown("#### "+record["id"]); details(record)
            with st.expander("Prepare a manual edit"):
                st.caption("Identifiers and currency are fixed. Commercial plan changes use an effective-dated amendment. Duplicate IDs cannot be edited.")
                if not hasattr(actions,"propose_record_edit"): st.info("Manual editing is unavailable in this version.")
                else:
                    from leakage.manual import EDITABLE_FIELDS
                    fields=st.multiselect("Fields to change",sorted(EDITABLE_FIELDS[ledger]),format_func=label,key="fields_"+ledger+record["id"])
                    with st.form("edit_"+ledger+record["id"]):
                        updates={}
                        for field in fields:
                            old=record.get(field)
                            if field=="status": updates[field]=st.selectbox(label(field),["issued","void"],index=1 if old=="void" else 0)
                            elif field.endswith("date") or field in ("period_start","period_end"): updates[field]=str(st.date_input(label(field),value=date.fromisoformat(old) if old else date.today()))
                            else: updates[field]=st.text_input(label(field),value=str(old or ""))
                        reason=st.text_area("Why should this record change?")
                        submit=st.form_submit_button("Create edit proposal",disabled=not fields)
                    if submit:
                        try:
                            proposal=actions.propose_record_edit(ledger,record["id"],updates,reason)
                            agent.store.displayed(thread,[p["key"] for p in actions.pending()])
                            agent.record(thread,"Prepare manual edit for "+record["id"],"Manual edit proposal created for review. Inspect the exact changes in Approvals.",selected_plan=proposal["plan_id"],selected_period=proposal.get("period"),findings=[])
                            st.session_state["ui_next_view"]="Approvals"; st.session_state["ui_notice"]="Review the before and after values, then approve or reject this edit."; st.rerun()
                        except (ValueError,OSError) as exc: st.error(str(exc))
            if ledger=="billing_plans":
                render_contract_amendment(record,"record-")
    else: st.info("No matching records. Try a different plan or search.")
with contracts_tab:
    if challenge_mode:
        st.subheader("Contracts")
        st.caption("Active sandbox billing plans. Approved commercial changes appear here; original supplied plans remain unchanged.")
        plans=source_data["billing_plans"]
        if plans:
            contract_id=st.selectbox("Open contract",[p.get("plan_id",p.get("id")) for p in plans],key="contract-selection")
            contract=next(p for p in plans if p.get("plan_id",p.get("id"))==contract_id)
            details(contract)
            st.info("Request contract creation or an effective-dated amendment in chat. Review the interpreted terms, dates, and proposed changes before approving them. Existing invoices are corrected separately.")
        else: st.info("No source contracts are available.")
    else:
        st.subheader("Contracts")
        st.caption("Billing plans are the authoritative contract records. Create agreed terms, then propose dated amendments. Invoice mismatches do not automatically change an agreement.")
        with st.expander("Create a contract"):
            st.caption("Enter agreed terms only. This prepares a proposal for approval; it does not issue invoices. Service end dates are exclusive.")
            with st.form("create_contract"):
                contract_id=st.text_input("Contract / plan ID",placeholder="P-NEW")
                customer_id=st.text_input("Customer ID")
                product_id=st.text_input("Product ID")
                from leakage.domain import CURRENCY_DIGITS
                currency=st.selectbox("Contract currency",list(CURRENCY_DIGITS))
                price=st.text_input("Agreed recurring amount")
                cadence=st.selectbox("Billing frequency",["monthly","quarterly","annual"])
                start=st.date_input("Contract start",value=date.today().replace(day=1))
                end=st.date_input("Contract end (exclusive)",value=date.today().replace(year=date.today().year+1,day=1))
                discount=st.text_input("Agreed discount (%)",value="0")
                proration=st.selectbox("Partial-period rule",["none","actual_days"],format_func=lambda v:{"none":"Full periods only","actual_days":"Prorate by actual days"}[v])
                source=st.text_area("Agreement reference / notes",help="Reference the approved agreement. Text is stored as source data, not agent instructions.")
                reason=st.text_area("Reason for creating this contract")
                create=st.form_submit_button("Preview contract creation")
            if create:
                try:
                    proposal=actions.propose_contract({"id":contract_id,"customer_id":customer_id,"product_id":product_id,"currency":currency,"amount":price,"cadence":cadence,"start_date":str(start),"end_date":str(end),"discount_percent":discount,"proration":proration,"notes":source},reason)
                    agent.store.displayed(thread,[p["key"] for p in actions.pending()])
                    agent.record(thread,"Prepare contract "+contract_id,"Contract creation is ready for review. No active contract or invoices have been created.",selected_plan=contract_id,selected_period=None,findings=[])
                    st.session_state["ui_next_view"]="Approvals"; st.session_state["ui_notice"]="Review the initial terms and billing schedule before creating this contract."; st.rerun()
                except (ValueError,OSError) as exc: st.error(str(exc))
        plans=state["billing_plans"]
        if plans:
            contract_id=st.selectbox("Open contract",[p["id"] for p in plans],key="contract-selection")
            contract=next(p for p in plans if p["id"]==contract_id)
            st.markdown("#### "+contract_id)
            st.caption("Customer "+contract["customer_id"]+" · Product "+contract["product_id"])
            st.write(amount(contract["amount"],contract["currency"])+" · "+contract["cadence"]+" · initial agreed amount")
            st.caption("Agreement window: "+period([contract["start_date"],contract["end_date"]]))
            if contract.get("notes"): st.write(contract["notes"])
            from leakage.domain import effective_terms
            versions=[{"Effective from":contract["start_date"],"Amount":amount(contract["amount"],contract["currency"]),"Discount (%)":str(contract.get("discount_percent","0")),"Reason":"Initial agreed terms"}]
            for amendment in sorted(contract.get("amendments",[]),key=lambda a:a["effective_date"]):
                terms=effective_terms(contract,date.fromisoformat(amendment["effective_date"]))
                versions.append({"Effective from":amendment["effective_date"],"Amount":amount(terms["amount"],contract["currency"]),"Discount (%)":str(terms["discount_percent"]),"Reason":amendment["reason"]})
            st.markdown("##### Agreement history"); st.table(versions)
            render_contract_amendment(contract,"contract-")
        else: st.info("No active contracts. Create a contract to begin.")
with approvals_tab:
    render_unresolved_drafts()
    render_approvals()
with activity_tab:
    render_history()
if agent.store.projection_error: st.warning(agent.store.projection_error)
