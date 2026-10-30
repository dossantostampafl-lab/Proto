# Runbook: setting up Oracle Cloud

This builds the layout of `docs/adr/venues-and-executor-hosting.md` with the Terraform in
`infra/oci/`. It stays on the **testnet and the MT5 demo**: no step here enables real money.

```text
internet ──443──> api (public subnet, the only public IP)
                   │ 8100 (intents + permits)
                   v
         executor (private) ──NAT, reserved IP──> Binance testnet
           │ 8200 (MT5 bridge)        ^
           v                          │ 8100 (status, halt)
         mt5 Windows VM (private)   watchdog (private) ──8000──> api
operator ──Bastion──> SSH (Linux) · RDP (Windows)
```

| Instance | Shape | Subnet | Notes |
|---|---|---|---|
| `proto-api` | A1 (Arm), 1 OCPU / 4 GB | public | Dashboard + API, TLS on 443 |
| `proto-executor` | A1, 1 OCPU / 4 GB | private, `10.20.2.10` | Only holder of venue credentials |
| `proto-watchdog` | A1, 1 OCPU / 4 GB | private, `10.20.2.11` | Halts only |
| `proto-mt5` | E5 (x86), 1 OCPU / 8 GB, Windows Server 2022 | private, `10.20.2.20` | XP MetaTrader 5 terminal |

Proto takes 3 of the 4 Always Free Arm OCPUs and 12 of the 24 GB. Each A1 instance needs at least
1 OCPU, and Chile West (Valparaiso) has no free AMD micro shape. An existing instance on the
same tenancy (for example The Creation) shares that allowance: check what it uses before applying.
Proto never shares an instance with another application: the API holds the permit signing key,
and the executor holds the venue credentials. The Windows VM
is billed: Windows needs an x86 shape and a license. Check the price in Oracle's cost estimator
before applying, or set `create_mt5 = false` until the XP path starts.

## 0. Make room in the Always Free allowance

The tenancy's free Arm allowance is 4 OCPUs and 24 GB in total, in the **home region only**
(this tenancy: Chile West, `sa-valparaiso-1`, which has no free AMD micro shape). Proto needs
3 OCPUs and 12 GB. Another instance gets the rest. For example, set The Creation to 1 OCPU and 12 GB:

1. Compute → Instances → `the-creation-os` → **Edit** → **Edit shape**.
2. Set **1 OCPU** and **12 GB**. Save. The instance reboots, about a minute offline.
3. Check it afterwards. Its `smoke-test.sh`, or the Deploy workflow's checks, should pass.

With 2 OCPUs kept for it, the account must be upgraded to Pay As You Go: the fifth OCPU is billed.
Free-tier accounts also get Arm capacity last ("Out of host capacity"), which an upgrade lifts.

## 1. Compartment and Cloud Shell

1. Console → Identity & Security → Compartments → **Create compartment** `proto`. Copy its OCID.
2. Open **Cloud Shell** (the `>_` icon at the top). It is already authenticated, and it has
   `terraform`, `git` and `oci`.
3. Find the home region: Governance → Tenancy details → Home region.

## 2. Apply the Terraform (Resource Manager)

Resource Manager is Oracle's managed Terraform. It authenticates by itself and keeps the state.
Cloud Shell's `oci` CLI is already authenticated, so it creates the stack:

```bash
git clone https://github.com/dossantostampafl-lab/Proto.git && cd Proto/infra/oci
zip -r ~/proto-oci.zip . -x '.terraform/*' '*.tfstate*' 'terraform.tfvars'
# Cloud Shell runs in FIPS mode: ED25519 keys are refused there, so use RSA.
ssh-keygen -t rsa -b 4096 -f ~/.ssh/proto -N "" -C proto-operator   # stays in Cloud Shell
COMP=<compartment_ocid>
cat > ~/proto-vars.json <<JSON
{
  "tenancy_ocid": "$OCI_TENANCY",
  "compartment_ocid": "$COMP",
  "region": "sa-valparaiso-1",
  "home_region": "sa-valparaiso-1",
  "ssh_public_key": "$(cat ~/.ssh/proto.pub)",
  "operator_cidrs": "[\"0.0.0.0/0\"]",
  "create_mt5": "false"
}
JSON
oci resource-manager stack create --compartment-id "$COMP" --display-name proto \
  --config-source ~/proto-oci.zip --variables file://~/proto-vars.json --terraform-version 1.5.x
```

Then, in the console, go to Developer Services → Resource Manager → Stacks → `proto`:

1. **Plan**. Read the log: about 40 resources, 3 instances, no secret.
2. **Apply**.
3. Read the outputs on the job's **Outputs** tab.

If the apply fails only with `Out of host capacity`, pin the fault domain of an instance that was
just shrunk, so the new instances land where it freed capacity. Find it with
`oci compute instance list --compartment-id $OCI_TENANCY --query 'data[].{n:"display-name",fd:"fault-domain"}'`,
then add `"fault_domain": "FAULT-DOMAIN-N"` to the stack variables.

**When Arm capacity is scarce, create the instances by hand.** In the console, create each one
(Compute → Create instance) on the existing `proto-vcn` subnet with its NSG, using the
`Create instance without specifying a fault domain` retry. Then give the stack its OCID:
`executor_instance_id`, `watchdog_instance_id` or `api_instance_id`. Terraform adopts it without
replacing it, and wires its dynamic group and its Vault policy.

`operator_cidrs` is `0.0.0.0/0` because Cloud Shell reaches the Bastion from inside Oracle's
network, not from the address `ifconfig.me` shows. An allowlist of that address makes the Bastion
close every session right after authentication. Access still needs IAM rights to create a
session and the session's own key; from a workstation with a fixed IP, a `/32` works.
`create_mt5 = false` leaves the billed
Windows VM out until the XP path starts.

Terraform from a workstation with `~/.oci/config` works too:
`cp terraform.tfvars.example terraform.tfvars`, then `terraform init && terraform apply`.

Keep the outputs:

- `nat_egress_ip`: the IP the Binance key is restricted to (step 4);
- `vault_id` and `secrets_key_id`: needed to create the secrets (step 3);
- `secret_names`: the secrets to create;
- `bastion_id` and `private_ips`.

The reserved NAT IP and the vault have `prevent_destroy`, so a destroy cannot silently change the
Binance allowlist or delete the secrets.

## 3. Create the secrets

Terraform never creates secret values, because they would be written to its state.
`infra/oci/scripts/create-secrets.sh` creates every name in `secret_names` from Cloud Shell:

```bash
oci resource-manager stack get-stack-tf-state --stack-id "$STACK" --file ~/proto.tfstate
bash ~/Proto/infra/oci/scripts/create-secrets.sh
```

- It generates the tokens, the TradingView secret, the MT5 bridge token, the permit key pair and
  both database URLs (with a random password). It prompts for the Binance testnet key and
  secret with `read -s`; Enter skips them until you have them.
- No value is printed, written to the shell history or passed on a command line: each request
  goes to the CLI as a 0600 file in a private temporary directory, deleted right after.
- It skips names that already exist, so running it again only adds what is missing.
- The permit pair comes from `infra/oci/scripts/ed25519_pair.py`, standard library only, because
  Cloud Shell runs in FIPS mode and refuses Ed25519 through OpenSSL. Its output has the same
  format as `python -m services.risk.permit_keys`.

| Secret | Value |
|---|---|
| `proto-operator-token` | 32+ random characters: `openssl rand -hex 32` |
| `proto-executor-token` | 32+ random characters. The API and the executor share it |
| `proto-watchdog-api-token` | 32+ random characters, different from the others |
| `proto-watchdog-executor-token` | 32+ random characters, different from the others |
| `proto-permit-private-key` / `proto-permit-public-key` | One Ed25519 pair. The executor gets only the public key |
| `proto-tradingview-secret` | Generated. Read it from the Vault console when you write the TradingView alert body |
| `proto-api-database-url` | `postgresql+asyncpg://proto:<password>@127.0.0.1:5432/proto` |
| `proto-executor-database-url` | `postgresql+asyncpg://executor:<password>@127.0.0.1:5432/executor` |
| `proto-binance-testnet-key` / `-secret` | From testnet.binance.vision (step 4) |
| `proto-mt5-bridge-token` | 32+ random characters, for the MT5 bridge (not built yet) |
| `proto-alpaca-executor-token` | Generated. The API and the Alpaca executor share it (optional Alpaca venue) |
| `proto-watchdog-alpaca-token` | Generated. The watchdog's halt-only token for the Alpaca executor |
| `proto-alpaca-paper-key` / `-secret` | Optional. From the Alpaca **paper** account (section "Alpaca paper key") |

Each instance reads only its own names (`infra/oci/iam.tf`). For example, the API cannot read the
Binance key.

## 4. Binance testnet key

1. Create the key at testnet.binance.vision (API Management, HMAC). Then add both halves in the
   Vault **console**, not in Cloud Shell: on a tablet, switching tabs to copy a key drops the
   Cloud Shell connection. In the console, go to Identity & Security → Vault, choose compartment
   `proto`, then `proto-vault` → Secrets → **Create secret**, twice:

   | Field | First secret | Second secret |
   |---|---|---|
   | Name | `proto-binance-testnet-key` | `proto-binance-testnet-secret` |
   | Encryption key | `proto-secrets` | `proto-secrets` |
   | Secret type template | Plain-Text | Plain-Text |
   | Secret contents | the **API Key** | the **Secret Key** |

   Then run `setup.sh` again; it deploys the executor once both exist.
2. On the live account later, for the canary, the key must be restricted to `nat_egress_ip`
   and allow spot trading only, with no withdrawals, transfers, margin or futures. The live
   canary checks this at start (`verify_trade_only`).


### Alpaca paper key (optional)

Only for the optional Alpaca US equities venue, PAPER account only (`docs/venues/alpaca-paper.md`).
Create the key in the Alpaca dashboard with the **Paper** account selected (never the live one),
then add both halves in the Vault console exactly like the Binance pair:

| Field | First secret | Second secret |
|---|---|---|
| Name | `proto-alpaca-paper-key` | `proto-alpaca-paper-secret` |
| Encryption key | `proto-secrets` | `proto-secrets` |
| Secret type template | Plain-Text | Plain-Text |
| Secret contents | the **API Key ID** | the **Secret Key** |

The executor instance reads them as `PROTO_CRED_ALPACA_US_TESTNET_MAIN_API_KEY` and
`PROTO_CRED_ALPACA_US_TESTNET_MAIN_API_SECRET` (fetch role `executor-alpaca`). The API and the
watchdog cannot read them. They are fetched apart from the executor's own secrets: while they are
missing only the Alpaca executor waits, and the Binance executor, the API and the watchdog run as
before. Nothing uses them until `/etc/proto/alpaca.conf` exists (see the venue doc).

If the executor stops with `binance -2014: API-key format invalid`, the value in the Vault is not
an API key: pasted with extra text, key and secret swapped, or created with the Base64 template
(the Vault then decodes it into noise). Open each secret, **Create secret version**, template
**Plain-Text**, paste only the key (or only the secret), and run `setup.sh` again.

## 5. Reach the instances (Bastion)

Console → Identity & Security → Bastion → `protobastion` → **Create session**:

- **Linux:** a *Managed SSH session* for `proto-executor`, `proto-watchdog` or `proto-api`,
  user `opc`, with your SSH key. The console shows the `ssh` command.
- **Windows:** a *Port forwarding session* to `10.20.2.20`, port `3389`. Then RDP to
  `localhost:<local port>`. The initial Administrator password is on the instance page
  (Instance access → Show initial credentials). Change it at first login.

## One command (after step 2)

Once the stack exists, one command in Cloud Shell does the rest, the secrets of step 3 included.
It is safe to run again at any time:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/dossantostampafl-lab/Proto/main/infra/oci/deploy/setup.sh)
```

`infra/oci/deploy/setup.sh`:

1. Syncs the stack with the repository and applies it. If the plan would destroy or replace
   anything, it stops and applies nothing.
2. Creates the missing Vault secrets (`create-secrets.sh`). It asks for nothing; the Binance
   testnet key and secret are added once in the Vault console (step 4).
3. Bootstraps the three instances over the Bastion and retries while the Bastion warms up.
4. Checks that each instance reads its own secrets. If one cannot, it tries the policy forms of
   `iam.tf` (`secret_policy_mode`) one at a time, each in a temporary policy that it deletes
   afterwards. It keeps the first form that works and applies it through the stack. Every form
   still restricts each instance to its own secret names.
5. Runs the deploy on each instance and prints a summary table: role, bootstrap, secrets and
   deploy.

It never prints a secret value. The log is in `~/proto-setup.log`.

## 6. Deploy from GitHub

The deploy is pull-based. GitHub only publishes images and holds no Oracle credential, and each
instance pulls its own tag on a timer. **Merging to `main` with green CI is the deploy.**

```text
merge to main -> CI green -> Deploy workflow -> ghcr.io/<owner>/proto:stable ----> api, watchdog
                                              \-> approval ("executor" environment) -> :executor -> executor
```

- `.github/workflows/deploy.yml` builds the arm64 image on a native Arm runner. It tags the
  image `:sha-<commit>` and `:stable`.
- The `executor` job moves `:executor` to the same digest only after an approval. The executor
  holds the venue credentials, so its image never changes without an operator.
- On each instance, `proto-deploy.timer` runs `proto-deploy` every 5 minutes:
  1. It pulls the role's tag. When the image changed, or nothing is running, it restarts the
     service.
  2. It checks the service's health. A failing release goes back to the last healthy image.
- Each service reads its secrets from `/run/proto/<role>.env` (tmpfs, step 3). Postgres gets
  its password from the database URL secret. Caddy terminates TLS on 443 in front of the API,
  for the `domain` stack variable or, without one, for `<api-public-ip-with-dashes>.sslip.io`
  (`setup.sh` prints the dashboard address at the end).

### One-time setup

1. **GitHub, repository settings:**
   - *Environments* → **New environment** `executor` → **Required reviewers**: yourself.
   - After the first Deploy run, open *Packages* → `proto` → *Package settings* → **Change
     visibility** → Public. The image holds no secret: the code is public, and secrets come
     from the Vault at run time.
2. **Each instance, once, from Cloud Shell.** This installs podman, the OCI CLI, the firewall
   rules, the secret unit and the deploy timer, over the Bastion (no public SSH):

   ```bash
   cd ~/Proto && git pull
   oci resource-manager stack get-stack-tf-state --stack-id "$STACK" --file ~/proto.tfstate
   bash infra/oci/deploy/bastion-run.sh bootstrap executor
   bash infra/oci/deploy/bastion-run.sh bootstrap watchdog
   bash infra/oci/deploy/bastion-run.sh bootstrap api            # or: ... bootstrap api <your domain>
   ```

   Running it again is safe, for example to set the domain later.
3. **Check:** `bash infra/oci/deploy/bastion-run.sh status <role>` shows the units, the
   containers and the last deploy log.

The executor starts with `EXECUTOR_ENVIRONMENT=TESTNET`, which is the only value its settings
accept. No step here enables real money.

To deploy new secrets without a new image, run `sudo proto-deploy --force` on the instance
(`bastion-run.sh ssh <role>`).

The ChatGPT connector token (`proto-mcp-api-token`, optional) is typed by the operator with
`create-secrets.sh`; a missing or invalid one leaves only the MCP server closed. Steps:
`docs/finrobots/chatgpt-mcp.md` ("Como ligar na Oracle").

## 7. MT5 on the Windows VM

1. RDP in through the Bastion (step 5).
2. Run XP's installer, `xpmt5setup.exe`. Windows must show **Verified publisher: MetaQuotes Ltd.**
3. Log in to the **demo** server first. Write down the server names (demo and real) and whether
   the account is *netting* or *hedge*. The account password stays on this VM.
4. Leave **Algotrading** off until the MT5 bridge runs. The bridge (`apps/mt5_bridge`) is the
   narrow RPC on port 8200 and refuses any non-demo account. The supported free path keeps the
   terminal and the bridge on your PC instead: `docs/runbooks/xp-node-on-pc.md`.

## Checks

- [ ] `terraform output nat_egress_ip` noted, for the Binance key.
- [ ] `curl -s http://10.20.2.10:8100/health` from the API instance answers
      `"venue_environment": "TESTNET"` and `"real_money_execution": false`.
- [ ] From any other address the executor is unreachable. The NSG allows only the API and the
      watchdog.
- [ ] The watchdog's heartbeat shows on the dashboard's Watchdog panel.
- [ ] The MT5 demo logs in. The server names and the account type are recorded.
