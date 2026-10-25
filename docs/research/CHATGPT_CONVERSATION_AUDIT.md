# Continuation: local OAuth and explanation conversations

The audit below describes the initial evidence-browser increment. It is retained
as history and superseded by this section where implementation status changed.

Implemented now: isolated PROTO OAuth CLI, refresh/sign-out, original provider
adaptation with no tools/fallback, loopback-only authenticated API, account-owned
SQL history, evidence snapshots and explanation UI. Disabled by default. See
[local setup](CHATGPT_LOCAL_SETUP.md) for commands, boundaries and remaining gaps.
Original source copies/hashes and adaptations are documented in
`third_party/the_creation_os/UPSTREAM.md`. No upstream license was invented.

No real account authorization, external inference, FinRobot research execution,
merge or deploy is claimed. Multi-user remote hosting and dashboard-native login
remain unimplemented. Direct trader chat is unavailable; the available path is
explicitly a ChatGPT explanation of registered evidence.

Local validation: Python 3.13 environment and network access restored; targeted
OAuth/storage/HTTP tests use simulated provider boundaries. Frontend typecheck,
contracts, build and bundle passed. Final CI status is attached to the PR revision.

## Historical first-increment audit

# ChatGPT + PROTO conversation — PARTIAL

Audit: 2026-10-24. Baseline PROTO `55f54fa0e6616acc7e40ef2d9dc40fbd2e33f3ae`.
Reference tree: The Creation OS `595b867e942fadab5668edaea0bf44af227fffb8`.
PRs #330 and #331 are merged; #329 is open and untouched.

## Delivered increment

The existing React dashboard now has an Agents view. It selects actual identities
from `/v1/agents/roster`, displays contracts and handler registration from
`/orchestration/status`, and reads the latest 20 existing decision-memory records.
Records are explicitly global, not attributed to the selected trader. A registered
contract is not called an executable agent. Conversation remains unavailable.
HTTP/network failure hides retained data; empty/configuration states are explicit.
Polling uses the existing bounded, same-origin client at 15-second intervals.
No new backend, persistence layer, credentials, inference cost or execution path.

Follow-up (Claude Code review): the view is now an Agents dashboard. Roster totals,
per-agent virtual book (equity, P&L, position, realized, valuation) and detail come from
`/v1/agents/roster`; decision evidence narrows to the selected agent's instrument
(`instrument_id`) and renders structured fields instead of raw JSON. Payloads are parsed
defensively and styles use the theme tokens. Validated: typecheck, build, contract-check,
bundle-check, and Chromium at 1440 px and 390 px against a local API (no console errors,
no horizontal overflow). Conversation remains unavailable.

## Official authentication compatibility

Sources consulted, not an OAuth end-to-end test:

- https://developers.openai.com/siwc
- https://developers.openai.com/siwc/token-sharing-open-source
- https://developers.openai.com/siwc/token-sharing-open-source/sign-in
- https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations
- https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server
- https://help.openai.com/en/articles/12584461-developer-mode-and-full-mcp-connectors-in-chatgpt

The documentation supports optional ChatGPT-plan use for eligible OSS/local apps;
remote/paid application availability needs separate confirmation. It documents
dynamic registration, stable host identity, PKCE, state, nonce and account-bound
issued client IDs. Identity alone does not authorize plan inference. HTTP inference
requires streaming, no provider persistence, and client-supplied context.
Do not infer hosted PROTO eligibility from a successful local login elsewhere.

The eight requested The Creation OS paths were retrieved for inspection:
`backend/app/inference/{chatgpt_connect,chatgpt_credentials,chatgpt_accounts,
chatgpt_provider,bootstrap,router}.py`, `backend/app/api/deus.py`, and
`backend/app/services/deus.py`. The connect implementation uses the documented
dynamic-registration flow; credentials handle refresh and scope checks, accounts
handle local sign-out and discovery-based revocation, and the provider implements
plan inference. Router/bootstrap contain fallback policies that must NOT become
an implicit paid fallback in PROTO.

No source was copied from The Creation OS. Its inspected tree contains no LICENSE
or NOTICE file; provenance/licensing must be resolved before redistribution.
No credential files were accessed or shared. Authentication is NOT IMPLEMENTED
in this increment and NOT VALIDATED with a real account.

## Existing architecture and reuse

- Trader identities: `services/agents/roster.py` and its existing API surface.
- Orchestration: `JOB_CATALOG`, supervisor, durable store and decision memory.
- Existing Creation bridge: authenticated missions with an allowlist of
  market-data-health, opportunity-scan and shadow-decision. It is a service
  identity, not an individual conversation-user identity. Do not expose its secret
  to a browser or blindly forward model-selected job names.
- Operator authentication is shared; it is insufficient for private multi-user
  conversation ownership. No chat messages are persisted under that identity.
- Research council/news/adversarial specialists in the staged adoption plan are
  future work, not advertised as installed conversation agents.
- FinRobot retains its original SDK, license, NOTICE and pinned provenance from
  #330/#331. Its isolated one-shot worker supports research, earnings, DCF, comps,
  IC memo and LBO. It is not connected to the API and currently takes provider
  keys. ChatGPT OAuth compatibility of that original SDK is unproven. No worker
  execution, mathematical rewrite or synthetic report was added.
- Risk Engine, permits, OMS, ledger, executor, reconciliation, watchdog and
  constitutional controls are untouched. The UI only calls existing GET routes;
  the roster GET retains its existing internal seed/fill synchronization behavior.

## MCP alternative and remaining gates

An authenticated MCP app can expose narrow evidence/research tools to ChatGPT
without making PROTO purchase OpenAI API inference. This places the intelligence
interface in ChatGPT; it does not automatically embed ChatGPT in the dashboard.
Eligibility, deployment endpoint, per-user authorization and tool permissions must
be verified. Never expose arbitrary job execution, operator secrets or financial
commands through MCP. No MCP server is deployed by this increment.

Remaining implementation: eligible deployment/account selection; isolated OAuth
registration and encrypted per-user tokens; refresh/revocation and session tests;
bounded inference adapter; authenticated conversation ownership and persistence;
research-only FinRobot job interface with enforced network isolation; context,
concurrency and usage budgets; executable specialist dispatch and sourced synthesis.
Do not enable a chat input until these paths can return real, traceable results.

## Validation and delivery limitations

- PASS: existing `node scripts/contract-check.mjs` (29 frontend source files).
- No simulated OAuth or research success is claimed.
- BLOCKED locally: clone and dependency installation (configured proxy port 8080
  refuses connections); Python is 3.12 rather than required 3.13; pytest and Ruff
  are absent. Source files were retrieved through the authorized GitHub connector.
- TypeScript, build, bundle, pytest, Ruff, integration, Playwright and CodeQL
  are not locally verified. CI must evaluate the exact PR commit.
- Graphify report is absent from the inspected tree; graph generation was not run.
- No merge, deploy or production smoke test. Draft PR only; not ready for integration.
- Rollback: revert the UI/docs commit. No database or credential migration exists.

Overall acceptance remains PARTIAL; authentication, conversation execution and
private history are BLOCKED, not delivered by the evidence browser.
