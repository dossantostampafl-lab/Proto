# PROTO + ChatGPT on the Oracle VM

Status: PARTIAL — implementation available for a private, single-user installation;
real OpenAI consent, account eligibility, inference and Oracle deployment have NOT
been tested. No paid API key or fallback is used. Public dashboard observed at
`https://155-181-40-136.sslip.io` (HTTP 200); that alone does not provide SSH access
or prove the connector is deployed.

Official procedure verified 2026-10-25:
https://developers.openai.com/siwc/token-sharing-open-source/self-hosted-vms
Sign-in contract:
https://developers.openai.com/siwc/token-sharing-open-source/sign-in

The browser computer completes OAuth for this user's PROTO registration. Transfer
that registration over SSH; the VM owns subsequent refreshes. Do not import The
Creation OS, Codex, browser cookies, or another user's credentials. Host-specific
usage attribution/revocation for transferred sessions is not yet available in the
OpenAI preview. Eligibility and consent are determined by OpenAI, not PROTO.

## 1. Prepare the private runtime on the VM

Use this branch's PROTO Python environment (Python 3.13, `pip install -e '.[dev]'`),
running as the same OS user as the API. From the PROTO checkout:

```sh
python -m services.chatgpt.vm prepare --profile default
```

This creates a stable, public host identifier under `~/.config/proto/chatgpt/host-id`.
It does not start OAuth or print credentials. Keep the identifier across restarts.
Apply the existing database migration procedure through `0014_agent_conversations`.
Retain existing database and financial settings; enable only the private connector:

```text
CHATGPT_LOCAL_ENABLED=true
CHATGPT_PROFILE=default
```

Keep the existing strong `OPERATOR_API_TOKEN` in protected runtime configuration.
Do not paste it into this document or a chat. Use durable control persistence
(`CONTROL_PERSISTENCE_ENABLED=true` with the existing durable `DATABASE_URL`).
`CHATGPT_MODEL` is optional: the dashboard can select from the authenticated model
catalog. The connector does not enable a trading mode or FinRobot execution.

Use one API worker, `--host 127.0.0.1 --port 8000 --no-proxy-headers`. Build the
existing dashboard with `npm ci && npm run build` in `apps/web`; serve it from
that same API. Do not run another independent frontend. If the VM uses containers,
map credentials only into the API container at its runtime user's private path;
never expose them to executor or research containers. Publish the API port only
on host loopback. Adapt service/container configuration to the actual deployment;
this document does not assume an SSH username, service manager or checkout path.

The repository's Oracle runtime uses root-managed Podman, the `proto` container
user (UID 10001), and Caddy. Store the credentials on the API VM only, for example
`/var/lib/proto/chatgpt`, owned by UID 10001, mode 0700. Mount this directory at
`/home/proto/.config/proto/chatgpt` in `proto-api` (read/write for token rotation,
with the appropriate SELinux label). Run prepare/import as that container user.
The current `proto-deploy` does not mount this path or opt in automatically:
preserve the mount and opt-in configuration in the API deployment before enabling
it, or the timer's next replacement will lose access. Do not modify the executor
or watchdog environment. The API can retain its private network listener for the
existing watchdog; conversation requests must still arrive through loopback.

Block this prefix on the PUBLIC Caddy proxy before opting in. Replace the current
one-line reverse-proxy command with a reviewed Caddyfile preserving the existing
domain, TLS storage and upstream. The equivalent route is:

```caddyfile
155-181-40-136.sslip.io {
    @conversation path /conversation /conversation/*
    respond @conversation 404
    reverse_proxy 127.0.0.1:8000
}
```

Retain all existing proxy security controls. Validate configuration before reload.
The private connector additionally rejects non-loopback clients, public hosts,
forwarding headers, cross-origin requests and missing operator authentication.
These checks do not replace the public proxy deny rule. Never expose port 8000
or OAuth callback port 1455 publicly.

## 2. Authorize directly with OpenAI on your own computer

Install this same PROTO revision and Python dependencies on Linux/macOS (or WSL
with a browser able to reach its localhost). From that checkout:

```sh
python -m services.chatgpt.chatgpt_connect --profile oracle --enable-plan
```

The tool opens the official `https://auth.openai.com/api/accounts/authorize` page
and listens on **your computer's** `127.0.0.1:1455`. Verify the OpenAI domain, choose
your intended account/workspace, and grant plan usage if eligible. A generic
OpenAI link cannot replace this per-attempt PKCE/state/nonce flow. The callback
validates the ID token and retains the issued registration before token exchange.

Protected file: `~/.config/proto/chatgpt/profiles/oracle/credentials.json`.
Do not open or paste its contents into the dashboard, Git, logs or chat. If plan
usage was declined or unavailable, the connector cannot perform inference. Retry
with the SAME profile and `--enable-plan`; do not repeatedly create registrations.

## 3. Transfer only the PROTO session over SSH

The repository's Oracle setup uses **`opc` through OCI Bastion**, with no public
SSH port. The dashboard URL does not provide SSH/OCI permissions. Use the existing
Bastion session and key, as described in
[Oracle setup](../runbooks/oracle-setup.md#5-reach-the-instances-bastion).
From a configured Oracle Cloud Shell, the existing helper opens an API shell:

```sh
bash infra/oci/deploy/bastion-run.sh ssh api
```

For transfer from your computer, create a Bastion port-forwarding session targeting
the API VM's **private port 22**. Use the SSH command provided by Oracle Console
to forward it to `127.0.0.1:2222` on your computer. Keep that session open; then:

```sh
ssh -p 2222 opc@127.0.0.1 'install -d -m 700 ~/.config/proto/chatgpt/transfer'
scp -P 2222 -p ~/.config/proto/chatgpt/profiles/oracle/credentials.json opc@127.0.0.1:.config/proto/chatgpt/transfer/credentials.json
```

Use the correct local key with `-i` if your SSH agent does not already provide it.
Verify the instance host key using the normal Oracle procedure. Do not disable
host-key verification, open public SSH, or alter Bastion access lists for this task.

For a native Python installation, on the VM, from its PROTO checkout/environment:
(the container procedure below applies to the repository's standard Podman setup)

```sh
chmod 600 ~/.config/proto/chatgpt/transfer/credentials.json
python -m services.chatgpt.vm import --profile default --source ~/.config/proto/chatgpt/transfer/credentials.json
rm ~/.config/proto/chatgpt/transfer/credentials.json
```

Import requires owner-only file/directory permissions, a PROTO issuer/registration,
account identity and plan scope. It preserves the VM host ID, ID token, refresh
metadata and issued client ID. It refuses another identity or replacing an active
session. Import checks the stored contract; it does not claim live authorization.

After successful import, remove the laptop's transferred `oracle/credentials.json`
using your local file manager. Do **not** sign out/revoke that laptop registration:
it is the same session now owned by the VM. Only the VM should refresh it. For
later reauthorization, securely transfer the VM's retained registration back to
the local PROTO profile, reuse that registration and its VM host ID, then repeat
the protected import after disconnecting the old session. Do not create a new
client to work around OpenAI limits or revocation.

For the repository's Podman installation, stage the transferred file into the
private mounted directory with UID 10001 and mode 0600, then use:

```sh
sudo podman exec proto-api python -m services.chatgpt.vm prepare
sudo podman exec proto-api python -m services.chatgpt.vm import --source /home/proto/.config/proto/chatgpt/transfer/credentials.json
```

Remove the staged and `opc` transfer files after success. Do not print them.
The API image must already contain this implementation. These steps require
privileged VM access; they have not been executed against the user's VM.

## 4. Open the private dashboard and verify access

From your computer, with the same Bastion forward to port 2222 still open, keep
this additional tunnel open:

```sh
ssh -p 2222 -N -L 127.0.0.1:18000:127.0.0.1:8000 opc@127.0.0.1
```

Open `http://127.0.0.1:18000` (the browser keeps the same origin through the tunnel).
In **System**, enter the existing operator token. In **Agents**, choose a real
agent and click **Verify OpenAI connection**. This first checks the private
credential record, then calls OpenAI's authenticated model catalog. Choose a
returned model, start a conversation, and ask about recorded evidence.

A successful model listing verifies account access at that moment; only a
successful message proves inference. Replies are explicitly **ChatGPT explanation**,
with evidence snapshots. They are not executions of the selected trader or
FinRobot. History is scoped to the verified account registration and agent.

**Disconnect ChatGPT** clears local bearer tokens and attempts only a discovered
OpenAI revocation endpoint. If remote revocation is unconfirmed, disconnect PROTO
in ChatGPT settings as well. No credentials are returned to the frontend.

## Verification and rollback

Offline tests explicitly simulate OAuth, credentials and inference. Required
release evidence: official consent completed; account model listing; one real
inference response with its origin/history; public `/conversation/` blocked;
private operator auth; disconnect/revocation result. Do not mark these validated
from unit tests or an HTTP 200 on the public dashboard.

Rollback: set `CHATGPT_LOCAL_ENABLED=false`, restart the private API using the
existing deployment process, and disconnect the PROTO session if intended. Leave
the public deny rule in place. Do not remove existing risk controls or financial
state. Conversation migration adds its own table and leaves existing tables intact.
