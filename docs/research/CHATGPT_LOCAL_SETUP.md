# Private local ChatGPT explanations — experimental, opt-in

This mode is for one user on a private PROTO installation. It is NOT a shared
hosted authentication service. Default is disabled. No live OAuth authorization
or inference has been validated in this development session.

## Connect on your own machine

Install PROTO's Python dependencies (Python 3.13), then run:

```sh
python -m services.chatgpt.chatgpt_connect --profile default
```

The system browser performs the official PKCE/OIDC authorization. PROTO stores
its own registration under `~/.config/proto/chatgpt/profiles/default/credentials.json`
(0600, parent 0700), outside Git. Do not copy The Creation OS credentials. Each
profile represents an account/workspace registration. Declining plan usage keeps
identity registration but refuses inference; `--enable-plan` explicitly requests
consent again. Login needs the host's port 1455 available.

Reauthorization reuses the issued client ID; `--new` never replaces a registration.
Treat profiles as private OS-user resources. Never mount them into research or
executor containers. Credential records are protected by OS permissions, not an
application-managed encryption key; use encrypted storage on the private host.

List models without exposing credentials:

```sh
python -m services.chatgpt.models --profile default
```

Choose `CHATGPT_MODEL` from the account's official `/v1/models` catalog (visible
slugs), not from guessed model names. This version does not provide a model picker.
Set `CHATGPT_LOCAL_ENABLED=true`, `CHATGPT_PROFILE=default`, `CHATGPT_MODEL` and a
strong `OPERATOR_API_TOKEN` in the private host's configuration. Use the existing
PROTO durable database/control persistence configuration; in-memory storage is
refused. Apply migration `0014_agent_conversations` using the existing migration
procedure, or the existing local schema initialization.

Run one API worker, bound to loopback, with forwarding headers disabled:

```sh
uvicorn apps.api.app.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers
```

Do not reverse-proxy this mode to the internet or share the operator token.
Loopback checks cannot establish physical network isolation behind a proxy.
Use the built same-origin dashboard at localhost:8000; set the operator token in
System. Select an actual agent in Agents, then start an explanation conversation.
Save its conversation ID to resume history. The account registration's identity
owns the history; another identity gets 404. Changing the local account is an
operator action, not a browser-selected profile.

The answer is labeled CHATGPT_EXPLANATION with an instrument-level evidence
snapshot. It is not a trader execution, FinRobot report or new risk decision.
No model-selected function, broker API or arbitrary job is dispatched.

## Limits and failure behavior

One in-flight conversation per API process; supported deployment is one worker.
At most 40 stored entries, 64 KB history, 32 KB request context, 16 KB evidence,
4,000 characters per question, 24,000 characters in an answer, 60 seconds inference.
No automatic retry or paid-provider fallback. Plan usage can still be consumed by
failed/cancelled requests; local limits are not billing guarantees. The provider
requires a completed stream before any reply is persisted. Failed replies are
shown as failures and not synthesized. History uses optimistic version checks.
Refresh, login and sign-out serialize per profile; lock waits are bounded.

Never paste secrets. Known credential values and common credential patterns are
rejected before message storage, but arbitrary secrets cannot be recognized
reliably. Prompts/results are not added to operational logs. SQL history contains
conversation content and evidence; protect the existing database and backups.

Disconnect locally (also attempts discovered remote revocation):

```sh
python -m services.chatgpt.chatgpt_accounts sign-out --profile default
```

If remote revocation is not confirmed, disconnect in ChatGPT settings too. Local
sign-out removes tokens, retains registration identity, and leaves private SQL
history intact. Login/disconnect UI and multi-user sessions remain future work.

## VM and other deployment types

Official VM procedure: https://developers.openai.com/siwc/token-sharing-open-source/self-hosted-vms
Complete OAuth locally for PROTO, preserve the VM's own host identity, and transfer
only that PROTO registration over a secure channel. Do not share files between
projects. The current HTTP surface remains loopback-only; use a private tunnel
only with the same-origin and non-forwarding constraints above. Shared/paid remote
hosting needs separately confirmed eligibility and a real per-user session layer.

## Validation

Offline tests use simulated OAuth/JWKS/inference and SQLite. They prove local
contracts, not account eligibility. Real OAuth E2E, original FinRobot execution,
shared hosting, remote revocation and production deployment remain unverified.
