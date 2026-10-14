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
| `proto-api` | A1 (Arm), 1 OCPU / 6 GB | public | Dashboard + API, TLS on 443 |
| `proto-executor` | A1, 1 OCPU / 6 GB | private, `10.20.2.10` | Only holder of venue credentials |
| `proto-watchdog` | A1, 1 OCPU / 4 GB | private, `10.20.2.11` | Halts only |
| `proto-mt5` | E5 (x86), 1 OCPU / 8 GB, Windows Server 2022 | private, `10.20.2.20` | XP MetaTrader 5 terminal |

The three A1 instances (3 OCPUs, 16 GB) fit Oracle's Always Free Arm allowance. The Windows VM
is billed: Windows needs an x86 shape and a license. Check the price in Oracle's cost estimator
before applying, or set `create_mt5 = false` until the XP path starts.

## 1. Compartment and Cloud Shell

1. Console → Identity & Security → Compartments → **Create compartment** `proto`. Copy its OCID.
2. Open **Cloud Shell** (the `>_` icon at the top). It is already authenticated, and it has
   `terraform`, `git` and `oci`.
3. Find the home region: Governance → Tenancy details → Home region.

## 2. Apply the Terraform

```bash
git clone https://github.com/dossantostampafl-lab/Proto.git && cd Proto/infra/oci
cp terraform.tfvars.example terraform.tfvars
# Fill in tenancy_ocid ($OCI_TENANCY), compartment_ocid, region, home_region,
# ssh_public_key and operator_cidrs (your IP: curl -s ifconfig.me, then add /32).
nano terraform.tfvars
terraform init
terraform plan -out proto.plan     # read it: 4 instances, 1 VCN, 1 vault, no secret
terraform apply proto.plan
terraform output
```

For a private repository, clone with a fine-grained read-only token, and do not save it.

Keep the outputs:

- `nat_egress_ip`: the IP the Binance key is restricted to (step 4);
- `vault_id` and `secrets_key_id`: needed to create the secrets (step 3);
- `secret_names`: the secrets to create;
- `bastion_id` and `private_ips`.

`terraform.tfvars` and the state stay in Cloud Shell and are git-ignored. The reserved NAT IP and
the vault have `prevent_destroy`, so a `terraform destroy` cannot silently change the Binance
allowlist or delete the secrets.

## 3. Create the secrets

Terraform never creates secret values, because they would be written to its state. Create each
name from `terraform output secret_names` in Cloud Shell. `read -s` keeps the value out of the
history and off the screen:

```bash
VAULT=$(terraform output -raw vault_id); KEY=$(terraform output -raw secrets_key_id)
COMP=<compartment_ocid>
new_secret() {  # new_secret <name>: prompts for the value, never echoes it
  read -rsp "$1: " v; echo
  oci vault secret create-base64 --compartment-id "$COMP" --vault-id "$VAULT" --key-id "$KEY" \
    --secret-name "$1" --secret-content-content "$(printf %s "$v" | base64 -w0)" >/dev/null && echo ok
  unset v
}
```

| Secret | Value |
|---|---|
| `proto-operator-token` | 32+ random characters: `openssl rand -hex 32` |
| `proto-executor-token` | 32+ random characters. The API and the executor share it |
| `proto-watchdog-api-token` | 32+ random characters, different from the others |
| `proto-watchdog-executor-token` | 32+ random characters, different from the others |
| `proto-permit-private-key` / `proto-permit-public-key` | One Ed25519 pair: `python -m services.risk.permit_keys`. The executor gets only the public key |
| `proto-tradingview-secret` | The secret in your TradingView alert body |
| `proto-api-database-url` | `postgresql+asyncpg://proto:<password>@127.0.0.1:5432/proto` |
| `proto-executor-database-url` | `postgresql+asyncpg://executor:<password>@127.0.0.1:5432/executor` |
| `proto-binance-testnet-key` / `-secret` | From testnet.binance.vision (step 4) |
| `proto-mt5-bridge-token` | 32+ random characters, for the MT5 bridge (not built yet) |

Each instance reads only its own names (`infra/oci/iam.tf`). For example, the API cannot read the
Binance key.

## 4. Binance testnet key

1. Create the key at testnet.binance.vision and save both halves as the two secrets above.
2. On the live account later, for the canary, the key must be restricted to `nat_egress_ip`
   and allow spot trading only, with no withdrawals, transfers, margin or futures. The live
   canary checks this at start (`verify_trade_only`).

## 5. Reach the instances (Bastion)

Console → Identity & Security → Bastion → `protobastion` → **Create session**:

- **Linux:** a *Managed SSH session* for `proto-executor`, `proto-watchdog` or `proto-api`,
  user `opc`, with your SSH key. The console shows the `ssh` command.
- **Windows:** a *Port forwarding session* to `10.20.2.20`, port `3389`. Then RDP to
  `localhost:<local port>`. The initial Administrator password is on the instance page
  (Instance access → Show initial credentials). Change it at first login.

## 6. Deploy on each Linux instance

Cloud-init already installed podman, git, the OCI CLI and the `proto-secrets` unit. Once the
secrets exist:

```bash
sudo systemctl start proto-secrets && sudo ls -l /run/proto   # <role>.env, 0600, in tmpfs
git clone https://github.com/dossantostampafl-lab/Proto.git /opt/proto && cd /opt/proto
sudo podman build -t proto .
```

- **Executor:**

  ```bash
  sudo podman run -d --name db --restart=always -e POSTGRES_USER=executor \
    -e POSTGRES_PASSWORD=<same as in the URL> -p 127.0.0.1:5432:5432 docker.io/library/postgres:16
  sudo podman run -d --name executor --restart=always --network host \
    --env-file /run/proto/executor.env -e EXECUTOR_ENVIRONMENT=TESTNET -e EXECUTOR_ACCOUNT=main \
    -e EXECUTOR_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT proto \
    uvicorn apps.executor.main:app --host 10.20.2.10 --port 8100
  ```

  First check the keys with `python -m services.venues.binance_smoke`, from the same env file.

- **API:**
  1. Run the image as in the README, with `--network host`, `--env-file /run/proto/api.env`,
     `-e OMS_EXECUTION_BACKEND=executor`, `-e EXECUTOR_URL=http://10.20.2.10:8100` and
     `-e PUBLIC_ORIGIN=https://<your domain>`. The NSG lets port 8000 in only from the watchdog.
  2. Run its own Postgres the same way.
  3. Put TLS in front with a reverse proxy on 443 (for example Caddy with your domain).
- **Watchdog:**

  ```bash
  sudo podman run -d --name watchdog --restart=always --network host --env-file /run/proto/watchdog.env \
    -e WATCHDOG_ID=oci-watchdog-1 -e WATCHDOG_API_URL=http://<api private ip>:8000 \
    -e WATCHDOG_EXECUTOR_URL=http://10.20.2.10:8100 proto python -m apps.watchdog
  ```

  Alternatively, install the systemd unit in `infra/watchdog/proto-watchdog.service`.

After a reboot, `proto-secrets` refills `/run/proto`, and the containers restart from it.

## 7. MT5 on the Windows VM

1. RDP in through the Bastion (step 5).
2. Run XP's installer, `xpmt5setup.exe`. Windows must show **Verified publisher: MetaQuotes Ltd.**
3. Log in to the **demo** server first. Write down the server names (demo and real) and whether
   the account is *netting* or *hedge*. The account password stays on this VM.
4. Leave **Algotrading** off until the MT5 bridge exists. The bridge is the narrow RPC on port
   8200, and it is not built yet.

## Checks

- [ ] `terraform output nat_egress_ip` noted, for the Binance key.
- [ ] `curl -s http://10.20.2.10:8100/health` from the API instance answers
      `"venue_environment": "TESTNET"` and `"real_money_execution": false`.
- [ ] From any other address the executor is unreachable. The NSG allows only the API and the
      watchdog.
- [ ] The watchdog's heartbeat shows on the dashboard's Watchdog panel.
- [ ] The MT5 demo logs in. The server names and the account type are recorded.
