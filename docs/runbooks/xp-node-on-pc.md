# Runbook: the XP (B3) node on your PC, reached from Oracle over Tailscale (free)

**Why this path.** XP only offers the MetaTrader 5 terminal, and MT5's Python API needs x86 Windows.
Oracle's Always Free tier has no Windows image, its x86 micro shape (1 GB, and not in Chile West)
cannot run it, and the free Ampere (Arm) shape would need x86 emulation with no known working report
for MT5 (see `docs/adr/xp-mt5-hosting-oracle.md`). So the terminal stays on the PC, and the Oracle
API reaches it over a private Tailscale network. Cost: **R$ 0** (Tailscale Personal is free; per its
pricing FAQ: up to 6 users, unlimited user devices).

```text
 Oracle (Valparaiso)                                   Your Windows PC
┌──────────────────────────┐      Tailscale        ┌────────────────────────────────────────┐
│ proto-api (container,    │  100.x.y.z:8101       │ XP executor  (binds the Tailscale IP)  │
│ host network) ───────────┼───────────────────────┼─> verifies Risk Permits, own ledger    │
│ XP_EXECUTOR_URL/TOKEN    │  encrypted, no        │      │ bearer token                    │
│ from /etc/proto/xp.env   │  inbound port opened  │      v                                 │
└──────────────────────────┘                       │ MT5 bridge (127.0.0.1:8200) ─> MT5 demo│
                                                   └────────────────────────────────────────┘
```

What this is **not**: the PC is not a trading host with real money. The bridge and the executor both
refuse a non-demo account. Everything is the XP **demo** account (`XPMT5-DEMO`).

## What you get, and what you give up

- B3 orders, quotes and the BRL account appear on the Oracle API and dashboard (the "XP / B3" lane).
- B3 trades Monday to Friday 10:00 to 17:55 (Brasilia). The node only needs to be up then; the
  scheduled task below covers 09:30 to 18:10.
- **If the PC is off or asleep** the API sees the XP executor as unavailable and refuses B3 risk
  (fail-closed). Binance/crypto keeps running: each venue answers on its own.
- Real money never goes through this path.

## Part 1: the PC (once)

1. **Tailscale.** Install it from tailscale.com/download, sign in (Google/Microsoft/GitHub account
   is enough). In the admin console, open the PC machine and **disable key expiry**, so the node does
   not drop off the network after 180 days.
2. **Python environment** in the repo (one time):
   ```powershell
   cd C:\Users\dossa\proto
   py -3.13 -m venv .venv313
   .\.venv313\Scripts\python -m pip install -e . MetaTrader5 tzdata
   ```
3. **MT5 terminal**: open it, log in to the XP demo account (File, Login to Trade Account, tick
   "Save account information"), Algotrading on. Add `PETR4F`, `VALE3F`, `BOVA11F` to Market Watch.
4. **The API's permit public key** (not a secret). In OCI Cloud Shell:
   ```bash
   oci secrets secret-bundle get-secret-bundle-by-name --vault-id "$VAULT" \
     --secret-name proto-permit-public-key \
     --query 'data."secret-bundle-content".content' --raw-output | base64 -d; echo
   ```
   The executor on the PC verifies the permits the production API signs with this key. A different
   key would make it refuse every order.
5. **Start the node** (replace the login and the key):
   ```powershell
   .\infra\xp-node\start-xp-node.ps1 -Login 511447388 -PermitPublicKey "<the public key>"
   ```
   It prints the bridge (`DEMO on XPMT5-DEMO`) and the executor (`ok (TESTNET)`) and stops if the
   terminal is logged in to anything but a demo account. For a local-only test use `-BindIp
   127.0.0.1`.
6. **Weekday schedule** (optional, PC clock must be Brasilia time):
   ```powershell
   .\infra\xp-node\register-xp-node-task.ps1 -Login 511447388 -PermitPublicKey "<the public key>"
   ```

## Part 2: Oracle (once)

1. In the Tailscale admin console create a **one-off, ephemeral-off, tagged auth key** (tag
   `tag:proto-api`). In the ACL, allow only `tag:proto-api` to reach this PC on port 8101:
   ```json
   {"acls": [{"action": "accept", "src": ["tag:proto-api"], "dst": ["<pc-name>:8101"]}]}
   ```
2. Open an **OCI Bastion managed SSH session** to `proto-api` (runbook `oracle-setup.md`, section 5)
   and install Tailscale on the instance:
   ```bash
   curl -fsSL https://tailscale.com/install.sh | sudo sh
   sudo tailscale up --auth-key=<the key> --hostname=proto-api --advertise-tags=tag:proto-api
   ```
3. Point the API at the node. On the instance (the token is read hidden; get it on the PC with
   `.\infra\xp-node\show-xp-node-token.ps1`, which copies it to the clipboard):
   ```bash
   curl -fsSL https://raw.githubusercontent.com/dossantostampafl-lab/Proto/main/infra/oci/deploy/set-xp-node.sh -o set-xp-node.sh
   sudo bash set-xp-node.sh <the PC's Tailscale IP>        # paste the token at the prompt
   ```
   It writes `/etc/proto/xp.env` (root only) and recreates the API container (`proto-deploy
   --force`). It refuses a public address and a weak token. `proto-deploy.sh` ignores a malformed
   file, so a typo cannot take the API down. To turn XP off again: `sudo bash set-xp-node.sh --remove`.
4. **Let the watchdog watch the node** (its own instance, its own halt-only token). Create a second
   auth key tagged `tag:proto-watchdog` and add it to the ACL source:
   `"src": ["tag:proto-api", "tag:proto-watchdog"]`. Open a Bastion session to `proto-watchdog`,
   install Tailscale (`--hostname=proto-watchdog --advertise-tags=tag:proto-watchdog`), then run
   the same `set-xp-node.sh` there with the token from
   `.\infra\xp-node\show-xp-node-token.ps1 -Watchdog`. On that instance it writes
   `WATCHDOG_XP_EXECUTOR_URL` and `WATCHDOG_XP_EXECUTOR_TOKEN` and recreates the watchdog.
   That token reads the node's status and halts it; it cannot place, release or cancel anything.
   An existing node gets this token the next time `start-xp-node.ps1` runs.

## Check it end to end

- On `proto-api`: `curl -s http://<pc-ip>:8101/health` gives `{"status":"ok","venue_environment":"TESTNET",...}`.
- `GET /v1/oms/ledger` on the public API carries an `xp_b3` block with the demo BRL balance.
- `GET /v1/agents/status` lists `XP_B3:PETR4F`, `VALE3F`, `BOVA11F`.
- The dashboard's FLOOR view shows the **XP / B3** lane as LIVE instead of NOT CONFIGURED.

## When something is off

| Symptom | Cause | What to do |
|---|---|---|
| Lane says NOT CONFIGURED | `/etc/proto/xp.env` missing or ignored | Run Part 2 step 3; the deploy log says `ignoring ... xp.env` when malformed. |
| Lane says UNAVAILABLE | PC off, node not started, Tailscale down, terminal logged out | Start the node; check `tailscale status` on both ends. |
| Lane says HALTED | A reconciliation divergence or the watchdog | Investigate; release only through the operator route (needs the token). |
| `Authorization failed (-6)` on the bridge | Terminal not logged in | Log in in the MT5 window, then start the node again. |
| Orders refused, `permit_bad_signature` | Wrong public key on the PC | Redo Part 1 step 4 and restart the node. |

## Limits to know

- The watchdog probes the XP executor (Part 2 step 4). A switched-off PC is reported, never a halt; a
  divergence halts the XP executor at once, a stale reconciliation only during the B3 session. It
  never halts the API or Binance for an XP problem.
- B3 quotes are kept in the API's memory; a read endpoint for them is a separate change.
- Whether the MT5 BRL balance moves when shares are bought decides if reconciliation stays
  consistent after a fill; that is proven only with a fill during a session.
- Each API restart resets the watchdog's 24 h coverage (the canary clock).
