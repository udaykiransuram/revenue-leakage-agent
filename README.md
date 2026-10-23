# Revenue Leakage Agent

[Live demo](https://revenue-leakage-agent-phi.vercel.app/)

A local Streamlit workspace for investigating the supplied billing records, explaining discrepancies with evidence, and applying explicitly approved corrections to a recoverable JSON sandbox. Python and LangGraph coordinate one tool-using application model; deterministic code performs financial calculations and enforces approval authority.

The authoritative input is `technical-case.zip`. Its four original JSON files are preserved in `data/`: **4 billing plans, 13 invoices, 1 credit memo, and 1 dated FX rate**. These are source counts; the active sandbox can contain additional approved records. Earlier synthetic active records and conversations were archived outside this deliverable. Synthetic fixtures and historical evaluation reports are separate development material, not evidence that the supplied dataset reconciles.

## Deploy to Vercel

See [DEPLOY_VERCEL.md](DEPLOY_VERCEL.md). The container preserves the Streamlit UI, uses Postgres for durable billing state and chat checkpoints, runs Groq with OpenRouter backup, and disables Ollama. A Vercel login and initialized Postgres database are required before publishing. Local source browsing and JSON/SQLite operation remain available.

## Start locally with Groq

Use Python 3.12. Install the pinned dependencies and configure the application model locally:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
cp .env.groq.example .env
# Set GROQ_API_KEY in .env. Keep credentials out of chat and version control.
.venv/bin/python -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501
```

Open `http://127.0.0.1:8501`. The credential-free template selects:

```dotenv
AGENT_MODE=live
MODEL_PROVIDER=groq
GROQ_MODEL=openai/gpt-oss-120b
GROQ_FALLBACK_MODELS=openai/gpt-oss-20b
OPENROUTER_BACKUP_ENABLED=true
OPENROUTER_BACKUP_MODEL=openrouter/free
OPENROUTER_API_KEY=
OLLAMA_BACKUP_ENABLED=true
OLLAMA_BACKUP_MODEL=qwen3.5:9b-q4_K_M
GROQ_API_KEY=
DATASET_MODE=challenge
LANGSMITH_TRACING=false
```

The application model is separate from the AI coding assistant used during development. Groq uses its OpenAI-compatible endpoint; selecting this provider does not require an OpenAI API key. Ollama is optional: enable the final local backup only when the configured model is installed and the loopback service is running. The application does not download models automatically. Without the Groq key, the app keeps source browsing available and displays configuration guidance; it does not substitute scripted answers. Use a Groq account with free-plan access. Account-specific request and token limits can interrupt a turn. On a rate limit, unavailable model, connection failure, timeout, or transient server error, the app tries the next configured Groq model. Set GROQ_FALLBACK_MODELS to a comma-separated list of up to two fallback model IDs, or leave it empty to disable switching. Each attempt is recorded in the trace with its model ID; successful fallback continues the same conversation and tool loop without replaying completed actions. All model attempts count toward the eight-call turn budget, and the successful fallback is retained for the remainder of that turn. Each new user message starts with the primary model again. Invalid requests and Groq authentication/permission errors stop without switching between Groq models. After the configured Groq models, OPENROUTER_BACKUP_ENABLED=true adds an OpenRouter backup when OPENROUTER_API_KEY is present. Its default OPENROUTER_BACKUP_MODEL=openrouter/free routes to free models with tool support. OLLAMA_BACKUP_ENABLED=true adds the installed local OLLAMA_BACKUP_MODEL as the final backup; its default context is 32768 tokens and timeout is 120 seconds, independently configurable via OLLAMA_BACKUP_NUM_CTX and OLLAMA_BACKUP_TIMEOUT_SECONDS. Each attempt records the actual provider and model. No completed tool operation is replayed on a switch. OpenRouter key or credit errors can fall through to local inference. The app does not enable paid billing; provider quotas and local availability can still prevent completion. Reload after changing local configuration.

## Interview workflow

The investigation workspace keeps conversation, evidence, exact proposal review, receipts, rollback, and the execution trace together. Contracts, Records, Approvals, and Activity & history remain available as secondary views.

1. Ask a factual question or investigate a source plan, for example: “Investigate C-1001. Separate established evidence from assumptions and explain any missing invoice candidate.” The model chooses tools dynamically; a currency question does not require a full portfolio investigation.
2. Review source evidence. The supplied invoices have issue/due dates but no structured service periods, and source plans omit exclusive term ends and structured recurring charges. A missing issue-month candidate is an observation, not automatic authority to create an invoice.
3. Review a proposed correction without first reconfirming every assumption in chat. The agent can include a complete proposed billing interpretation with the invoice or credit: charge per billing period, exclusive term end, every linked invoice's service allocation, proration, FX date policy, entitlement scope, and audit cutoff. The review card shows those assumptions and the exact financial change together. **Approval confirms the displayed assumptions and applies that correction together; rejection changes neither.** A separate context-only proposal remains available and creates no invoice or credit.
4. Reconcile using the approved interpretation. Expected and actual amounts, dated FX, existing credits, evidence references, and unresolved issues remain visible. Ask the agent to explain or revise its proposal if the interpretation is wrong.
5. Review the make-good invoice, credit memo, or commercial amendment. If assumptions were approved separately, approve the financial proposal separately; a combined proposal needs one approval covering its displayed assumptions and correction. **Only explicit approval of the financial revision authorizes the displayed change.** Changing a draft requires fresh review.
6. After application, inspect the receipt, current records, and fresh verification. Restart the app and select the same conversation to resume. Request rollback of the latest eligible action, inspect the stored before-images, and approve the rollback separately.

A demonstration may supply hypothetical commercial facts, but must label them as user-confirmed demo assumptions rather than facts already contained in the source files. Missing FX rates, entitlement allocation, unsupported cadence/rules, conflicting IDs, or incomplete service mapping stay unresolved. Existing credits reduce net billed revenue; a paid invoice's net billed amount is not an outstanding payable balance.

## Original source schemas

The adapter preserves native field names and identifiers:

| File | Supplied fields |
| --- | --- |
| `data/billing_plans.json` | `plan_id`, `customer_name`, `total_value`, `currency`, `cadence`, `start_date`, `entitlements`, `notes`; amendment plan also has `amends` |
| `data/invoices.json` | `invoice_id`, `plan_id`, `customer_name`, `issue_date`, `due_date`, `amount_invoiced`, `currency`, `status`, `description` |
| `data/credit_memos.json` | `memo_id`, `plan_id`, `invoice_id`, `amount`, `currency`, `issue_date`, `reason` |
| `data/exchange_rates.json` | `date`, `from_currency`, `to_currency`, `rate` |

`total_value` is not silently interpreted as a per-period charge. Source notes can suggest an interpretation but are untrusted evidence, never instructions or approval. Empty plan references remain orphan evidence; matching customer names alone does not attach them to a contract. Amendment linkage is explicit: `C-1007-A1` amends `C-1007` from its source start date. Source issue dates are not silently substituted for service periods. Exact-date FX comes only from supplied rates; original invoice amounts/currencies remain intact.

For linked source plans, `reconcile_plan` also returns an `agreement_billing_summary` from either the original or amendment ID. It traverses explicit amendment links and totals all issued invoice history, with original-currency details, gross billing, credits, net billing and a per-plan breakdown in the original plan currency. Identical repeated invoice/credit IDs count once; void invoices are excluded. Paid and unpaid invoices both contribute to billed amounts. Invoice and credit FX use their respective issue dates. Missing FX, conflicting IDs or invalid relationships withhold the combined total. For C-1007 and C-1007-A1, the supplied evidence gives USD 47,000 gross minus USD 2,000 credits = USD 45,000 net billed. This summary is separate from each version's service-period reconciliation: contract values are not added, and a billed total does not authorize a correction or establish unpaid debt. Portfolio results include the agreement summary once.

Approved context records live separately in the sandbox. The supported context requires `period_amount`, `term_end`, `invoice_periods` mapping each nonvoid linked invoice to `[start, end]`, `proration` (`none` or `actual_days`), `fx_date_policy` (`invoice_issue_date`), and `entitlement_scope` (`all_plan_entitlements`). End dates are exclusive. Partial or unknown entitlement coverage cannot support a financial conclusion. Billing anchors follow the source contract start, including noncalendar dates. Unsupported allocation and commercial rules require further clarification or an adapter.

Unassigned invoices also appear as `possible_orphan_invoice_match` suggestions in plan investigations and portfolio results. Customer names are matched ignoring case and repeated whitespace; issue dates suggest versions using their start and superseding amendment dates. Other customer agreements remain separate possible matches. These suggestions show the original amount, date, payment status, candidate plan IDs and date evidence without inferring service periods or contract renewal coverage. I-9202 is therefore suggested for review against C-1007-A1 (USD 15,000, issued 2025-12-10, paid), while the linked agreement's USD 45,000 net billed remains unchanged. Missing/invalid dates and conflicting invoice IDs prevent a specific plan suggestion. Contract reference and service-period confirmation are required before including an orphan in agreement billing; this feature does not assign or mutate records.

## Agent and tool boundary

`leakage/agent.py` runs the bounded LangGraph model/tool loop. Conversation checkpoints retain messages, tool results, selected scope, findings, trace events, and proposal references across restarts. The model has up to eight iterations per turn. Tool timeouts, limited transient read retries, and explicit failure results keep partial work inspectable. Hidden model reasoning is never displayed.

The eight challenge interfaces remain available: `load_plan`, `query_invoices`, `fx_convert`, `propose_make_good_invoice`, `propose_credit_memo`, `propose_plan_amendment`, `apply`, and `rollback`. Additional tools inspect evidence and history, reconcile plans/portfolio, select service periods, and propose/read billing context. The model can prepare a proposal; it cannot grant itself approval by changing an argument, source note, or approval flag.

`leakage/challenge_data.py` reads native source evidence. `leakage/source_billing.py` validates explicit context and performs Decimal-based, currency-aware schedule/reconciliation calculations. `leakage/source_actions.py` maps reviewed proposals to native sandbox records. The action factory selects the source engine for native challenge records. Proposals use stored immutable revisions, affected-source fingerprints, exact displayed approval, receipt-based idempotency, and stored before-images. A stale proposal returns for review. Batch approvals execute sequentially and report each result; they are not an atomic batch transaction.

## Persistence, recovery, and reset

Original `data/*.json` files remain immutable. `sandbox/snapshot.json` is the canonical committed state: active financial records, contexts, proposals, receipts, audit events, and conversation metadata. Updates replace this snapshot atomically under a process/file lock. Separate `sandbox/*.json` ledgers and `sandbox/audit_log.json` are projections repaired from the snapshot when needed. LangGraph's SQLite checkpoints preserve conversation state. Keep both the sandbox and checkpoint database when moving a working investigation.

The UI displays **active sandbox** counts and records, including approved corrections. An “Original supplied evidence” section keeps original records distinguishable from those active records. After a mutation the application reloads and verifies affected records. Restart recovery checks stored receipts before any replay; repeated application of the same approved proposal revision returns its existing receipt. Rollback preserves audit history and restores a saved before-image only when no later dependent change would be overwritten.

To reset, stop Streamlit first. The command archives the current sandbox before rebuilding it from the original files:

```sh
DATASET_MODE=challenge .venv/bin/python -m leakage.cli reset --confirm RESET
```

The old scripted `demo` and `evaluate` CLI paths describe synthetic fixtures and should not be used as acceptance results for the actual source dataset.

## Execution trace and privacy

The visible Agent trace shows the latest request's actual model/tool/approval steps, status, measured duration, token usage when available, and evidence references. Trace events persist in the checkpoint but are not included in model prompts. Trace summaries contain structural execution facts, not hidden reasoning, system prompts, credentials, headers, raw exception payloads, or copied ledger prose. It updates after execution; live streaming of individual steps is not implemented. Factual tool outputs and audit history are available separately for inspection.

Groq inference sends the conversation and tool evidence needed by the agent to the configured hosted model. If enabled and reached, the OpenRouter backup receives the same conversation and tool evidence and routes it to its selected model provider. The final Ollama backup uses an installed model through an HTTP loopback address. Ledger storage and mutation remain local. No external billing system is connected. Credentials belong only in the local environment; `.env` must not be committed.

## Verification status

Verification is layered; offline or scripted results do not count as live-model acceptance. Synthetic live probes with empty billing ledgers verified OpenRouter returning a response after a simulated Groq rate limit, and installed local Ollama returning a response after simulated hosted failures. These probes establish fallback transport, not supplied-data financial lifecycle acceptance.

- Supplied-source read checks verified native IDs/counts, customer queries, missing-issue-month candidates, exact dated FX with the existing credit, and orphan evidence. `groq_source_check.json` records a real Groq read-only tool loop; its initial failed currency-label run is retained separately rather than counted as a pass.
- `groq_trace_check.json` records a real model → tool → model execution with persisted trace and unchanged financial revision. These checks establish transport, tool use, and the exercised facts; they do not establish universal conversational accuracy.
- Focused Streamlit checks verified context-only approval presentation, native amendment/invoice previews, active sandbox record visibility without original-file mutation, and latest-run trace presentation without raw errors. These are isolated fixtures and mocked/injected UI states, not live model runs.
- **Current source integration:** 31 focused core tests passed, including a conversation through clarification → context approval → financial proposal → approval → verification → restart → rollback. The conversation test injects a queued model and proves tool/approval/state behavior; it is not live inference. Five focused UI checks also passed. After explicit sharing authorization, a fresh live Groq check retrieved records, requested clarifications and generated a context proposal. The run encountered free-tier rate limits and wrong context field names; a typed tool schema now exposes exact required fields (seven relevant integration/config checks passed). Review rejected the final test proposal because the model omitted the requested audit cutoff. No live financial action was applied. `groq_lifecycle_check.json` records this partial, failed acceptance honestly; complete live-model lifecycle acceptance remains unverified.

Relevant focused tests, after integration, can be run with:

```sh
.venv/bin/python -m pytest tests/test_source_conversation.py tests/test_source_billing.py tests/test_source_actions.py tests/test_trace.py tests/test_groq.py -q
.venv/bin/python -m pytest tests/test_ui.py -q -k 'source_billing_context or source_native_amendment or source_records_show_active or agent_trace'
```

Historical synthetic reports, long-context model checks, and earlier UI totals are not current supplied-data certification. Sponsor-model validation and private GitHub submission remain separate acceptance steps. Source-native contract creation, direct raw-record rewriting, partial-entitlement billing, arbitrary commercial rules, and unsupported date/FX conventions require additional reviewed adapters; no claim of universal ambiguity resolution is made.

## Challenge materials and AI assistance

The original brief is preserved in `materials/technical-case-readme.md`, and the earlier PDF in `materials/challenge.pdf`. `candidate_prompts.md` records actual AI coding prompts, supplied context, iterations, and reflections. The original archive's Git metadata and hooks are not executed or imported. Explain explicit assumptions, unverified cases, and remaining limitations in the interview rather than presenting a successful demo as broader certification.
