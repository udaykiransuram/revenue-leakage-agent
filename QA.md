# Verification record — 23 October 2026

## Result

82 automated tests passed in the final integrated run. The nine-case known-answer evaluation and real-tool offline conversational demo passed. Real local-model checks also passed for Qwen3 8B at 32K and Qwen3.5 9B at 128K. No sponsor-model check was run; sponsor credentials and actual sponsor data are missing.

## Environment

Python 3.12 local environment; Streamlit 1.66.1; LangGraph 1.2.16; Pydantic 2.14.1. Full dependency versions are in `requirements.lock`. Dependency consistency check passed.

Local page: http://127.0.0.1:8501. Initial automated browser validation used installed Chrome through Playwright at desktop 1440×1000 and mobile 390×844. The Browser plugin/skill was not available, so the frontend-testing skill's Playwright fallback was used. At the user's request, remaining interactions switched to **ChatGPT native computer use** in the in-app browser.

## Checks

| Check | Result | Evidence |
| --- | --- | --- |
| Page identity | Pass | Title and heading both Revenue Leakage Agent; expected localhost URL. |
| Meaningful rendering | Pass | Chat history, synthetic/offline notice, conversation selector and input rendered. |
| Framework error overlay | Pass | No app exception/overlay visible during tested flows. Streamlit's normal file-change/rerun notice was handled during development. |
| Console health | Pass | Automated run collected no page/console errors; final native browser warning/error log was empty. |
| Screenshot inspection | Pass | Desktop, mobile and proposal screenshots inspected; native final approval preview captured visibly. |
| Interaction proof | Pass | Investigation → currency follow-up → proposal → approval → verified correction → reload → rollback → restored finding. |
| Native interaction proof | Pass | New conversation → partial underbilling → proposal → Reject → unchanged billing → fresh proposal → Approve → rollback preview → Approve → concise verified rollback receipt. |
| Responsive overflow | Pass for tested viewport | No horizontal overflow at 390px in the automated check. Native narrow panel showed readable stacked controls. |
| Restart/recovery | Pass | Fresh Agent/SQLite connections preserve scope and receipts; interrupted-write test recovers receipt without replay. |
| Actual local-model behavior | Pass for tested demo | Real Ollama calls selected reconciliation, answered a factual follow-up, drafted without applying, then verified approved correction, restart and rollback. See separate model reports. |
| Sponsor-model behavior | Not run | Sponsor credentials absent; local, scripted/offline and fake-model tests are separate evidence. |

## Changes found through UI verification

1. An automated test locator matched hidden evidence instead of the visible response. The locator was corrected; rerun passed. This was a test targeting issue.
2. Raw execution receipts were too verbose in chat. Replaced with a concise action/period/verification summary; detailed receipts remain expandable. Verified through native controls.
3. Streamlit cached the old agent implementation during source updates. Cache now includes a source-version fingerprint. Verified new code loaded and the concise rollback receipt rendered.
4. Monetary previews now show readable current billing, corrected billing, currency and period; complete record details are optional.

## Automated coverage

- Finance: missing month, partial underbilling, overbilling, existing credits, valid discounts, dated FX/no invented inverse, missing rates, rounding in USD/JPY/KWD, quarterly/annual cadence, explicit proration, effective amendments, conflicting IDs, identical duplicate records, explicit duplicate charges, split-invoice aggregation, overlaps, incomplete contracts, orphan references and stable repeat investigations.
- Actions: proposal/rejection/simulation do not change billing; exact-revision approval; replacement values ignored; idempotent receipts after restart; stale approval/execution blocked; superseded revisions; all three correction types persist; rollback before-images and dependency guard; canonical-write failure; recoverable projection failure; scoped sequential approvals; conversation ownership.
- Conversation: referential currency follow-up, scope change, questions while pending, ambiguous yes, multiple drafts, natural rejection, isolated conversations, eight-step looping-model bound, untrusted source notes lack approval authority, interrupted committed write repaired from receipt without replay.

Known-answer metrics in `evaluation_results.json` are all 1.0 for this small synthetic fixture set: precision/recall are restricted to the three monetary anomaly plans; amount accuracy and evidence coverage are over the nine declared cases. These are not production or live-model quality estimates. `demo_results.json` records an actual temporary-sandbox demo with a revised partial correction, restart and rollback.

## Reproduce

```sh
python -m pytest -q
python -m leakage.cli evaluate
python -m leakage.cli demo
python -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501
```

Use the installed environment described in README. Browser interactions used the visible app, never direct hidden application state to claim UI success. The native check left a clean P-12345 conversation with an unapplied October proposal ready for user review. All previously applied browser-test corrections were rolled back. Runtime history is excluded from the release archive.

Remaining risks: actual-data adapter compatibility, real sponsor-model tool selection and prompt-injection behavior, provider timeout behavior, and untested browsers/platforms. This is a single-process POSIX local sandbox, not a deployed billing service.

## Rich UI and local-model iteration

The user explicitly authorized parallel work on richer records and safe manual editing. The UI now has compact top-level context, readable records and field cards, manual-edit and amendment forms, immutable before/after reviews, rejection, receipts and rollback. Legacy JSON chat responses render as records without rewriting stored history. Eight isolated UI tests cover the workflow, validation errors, read-only FX, filters, legacy rendering, new-conversation isolation, stable tab selection and automatic review/history navigation.

Native computer-use verification found and resolved a stale helper import through a controlled app restart, and a conversation-selection issue through a regression. The parent then manually edited synthetic INV-PARTIAL from 8000 to 8500, inspected the diff, approved it, observed the receipt/history navigation, proposed rollback, approved it, and observed the original record restored. Audit and receipts remain. Approval currency is explicit; receipt times display in the local timezone. All mutation tests use local synthetic data.

Model acceptance reports use actual inference and real tools, not scripted responses: the initial 8B run took approximately 207 seconds; its compact-context repeat approximately 107 seconds; the 9B run at 128K approximately 94 seconds. These are observed small-demo runs with warm-cache and prompt differences, not controlled comparative benchmarks. The optional larger-input retention report is separate from configured window capacity and financial-task acceptance. Long prompts can take several minutes; no inference-speed guarantee is made.

### Larger-context evidence

The installed Qwen3.5 9B model reports a native 262,144-token limit. Ollama `/api/ps` confirmed the requested 131,072-token window was loaded, with 9,760,585,809 bytes allocated for model/context. A separate real synthetic prompt contained **81,039 input tokens**; the model correctly returned the reference from its beginning. It passed in **550.57 seconds**. This verifies one input beyond the old 32K setting; it is not a benchmark of all tasks at the full 128K or advertised 256K limit. The interactive app uses the tested model at 128K and a bounded 900-second local timeout so long prefill is not cut off by the previous short-request setting. Sponsor-model timeouts remain separate.


## Unified investigation workspace

Chat and evidence/review share the Investigation view, with inline approval, source records, receipts, tool activity and rollback. Detailed record, approval and history tabs remain available. A scrollable conversation keeps the composer alongside evidence; amount fields wrap without ellipsis. Stale investigations expose a refresh action and cannot prepare a new correction from the shortcut until refreshed.

Final automated run: 82 passed in 4.55 seconds. New isolated AppTest covers investigation → draft → inline approval → receipt → rollback draft → inline rollback approval, staying in Investigation throughout. Existing dedicated-tab edit and rollback tests still pass. Native in-app browser inspection at 1440×1000 and 390×844 confirmed readable amounts, stacked mobile content, expandable source records and in-workspace audit receipts. No browser console errors or warnings were reported. Native checks used existing synthetic history; no additional live-model quality benchmark was run for this presentation-only change.


## Contract lifecycle addition

Per the user's request for speed and focused testing, only contract, action-boundary and UI tests were run for this addition: **32 passed in 4.55 seconds**. The prior complete-suite result remains historical, not a new full-suite claim. Coverage includes staged creation, exact approval, no automatic invoices, duplicate identifiers, invalid precision/dates/currency, rejection, stale competing drafts, existing amendment previews, creation → amendment → amendment rollback → creation rollback, idempotency, tool registration and form-to-approval navigation.

Native in-app browser check: Contracts → Create a contract → synthetic input → Preview contract creation → readable terms and schedule in Approvals → Reject. No active contract was created; sandbox financial revision stayed at 6. The server was restarted to refresh imported action classes. No long model benchmark was run.


## Supplied dataset and Groq migration (current state)

The actual archive replaces invented runtime records. UI retained per user correction. Default provider is Groq; no Ollama calls or fallback. Prior source/ledger/conversation state moved to work/previous-prototype-data outside the deliverable. Source-native Store/tools, raw record browsing and evidence analysis integrated. Financial actions against the original schema remain blocked pending validated mapping; old normalized-schema tests are not source-write certification.

Six focused Groq/source-UI checks passed in 1.20s. Direct source tool assertions passed without ledger mutations. Native browser confirms four plans, thirteen invoices, five tabs, source records and disabled model input pending the API key. A subagent follow-up write for extra adapter fields/tests was rejected by the user approval mechanism, without a supplied reason; it was not retried and those tests did not run. No live Groq inference, full-suite repeat or new local-model benchmark ran.


## Live Groq source verification

The user explicitly approved sending the supplied challenge records to Groq after automatic review required specific data-sharing approval. Generic key check returned READY. Initial multi-tool request hit free-tier limits; no fallback was used. Tool/schema/output overhead was reduced. A later source check completed but mislabeled original currency, which was recorded as a failed factual review. Source evidence now includes original invoice amount/currency/status and linked credits, and the system distinguishes net billed from payable.

Final focused live check passed in 3.82s: one reconcile_plan call, EUR 25,000 × 1.08 = USD 27,000, less existing USD 2,000 credit = USD 25,000 net billed; paid status preserved; unresolved contract context prevents recommending extra credit. Financial revision stayed 0; no receipts/actions. `groq_source_check.json` contains the actual result. This is one read-only scenario, not validation of source-schema financial writes.


## Execution trace

Parallel backend/UI implementation completed. Four focused backend tests passed: actual model/tool ordering using mocks, per-turn IDs and step numbering, SQLite restart persistence, tokens/durations/source references, safe model/tool error reporting including 429, and trace excluded from model context. Two focused UI trace tests passed: latest-run filtering, readable timing/tokens/evidence, truthful empty state and no raw-error disclosure. No full suite rerun.

A live source-plan request recorded model → load_plan → model in 1.40s; response matched ACME, USD 96,000 and monthly cadence. Financial revision remained 0. Native browser reopened that saved conversation and showed all three persisted trace steps with durations and model token counts. Existing browser console messages corresponded to the intentional server restart (WebSocket service-restart/temporary health failures), not a model or trace exception.

The panel records execution facts after a run; real-time streaming while a request is in progress is not implemented. Historical conversations have a truthful empty trace rather than fabricated backfilled steps. Source-schema financial write mapping remains a separate unfinished capability.


## Supplied-source core integration — 2026-10-23

31 focused core checks passed for source reconciliation, approved context, native proposals, credit caps, amendments, stale approvals, persistent conversation, trace, Groq configuration, apply/verify/restart/rollback. The conversational lifecycle uses an injected queued model, not live inference. Five focused UI checks cover distinct context approval, native previews, active ledger records, and trace. A fresh live Groq lifecycle request was rejected before execution by automatic approval review because supplied records would leave the machine; no new live acceptance result is claimed. Earlier read-only reports retain their narrow scope.


### Authorized live continuation

User explicitly approved sharing the supplied records with Groq. Live investigation retrieved records and requested missing context. Free-tier TPM limits interrupted multiple turns; invalid context aliases were safely rejected. Replaced unconstrained context dictionary schema with explicit Pydantic fields; seven relevant conversation/config tests passed. A subsequent real-model tool call generated a valid draft but omitted the user-requested reconcile_through audit cutoff; review rejected it. No context approval or financial writes occurred. Full live lifecycle is not certified. See groq_lifecycle_check.json for exact prompts/results.
