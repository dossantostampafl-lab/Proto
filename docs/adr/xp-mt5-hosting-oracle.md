# ADR: Hosting the XP MT5 terminal for the Oracle deployment

- Status: **Option C is now the chosen path, with a private Tailscale link: the terminal stays on
  the operator's PC and the Oracle API reaches it, at no cost.** See
  `docs/runbooks/xp-node-on-pc.md` (scripts in `infra/xp-node/`, Oracle side `infra/oci/deploy/set-xp-node.sh`).
  Option A (Windows VM on OCI) stays the way to a 24/7 host if the cost is ever accepted.
  Nothing here is applied to the Oracle stack until the runbook is followed.
- Context: `docs/adr/venues-and-executor-hosting.md`, `docs/adr/xp-routing-in-the-api.md`.

## Problem

XP offers no order API to retail accounts, only the MetaTrader 5 terminal. MT5's Python API runs
only on Windows, and Windows runs only on x86. The Oracle stack runs on Ampere A1 (Arm) instances.
The terminal therefore needs a host of its own, reachable only by the XP executor.

## What already exists

- Terraform for the VM (`infra/oci/compute.tf`, variable `create_mt5`, shape `VM.Standard.E5.Flex`,
  1 OCPU / 8 GB, Windows Server 2022, fixed private IP `10.20.2.20`, its own NSG, bridge port 8200).
  Note: `create_mt5` defaults to **true** in `variables.tf`, while the runbook says to set it to
  `false` until the XP path starts. Treat the default as a trap and set it explicitly.
- The bridge (`apps/mt5_bridge`), the XP adapter and, since the executor-per-venue change, an
  executor that serves XP_B3 (`EXECUTOR_VENUE=XP_B3`).
- Runbook step 7 (`docs/runbooks/oracle-setup.md`): installer, demo login, Algotrading.
- The bridge now also runs in **attach mode** (no `MT5_PASSWORD`): it uses a terminal that is
  already logged in and checks the account and server. On a VM this avoids keeping the XP password
  in the Vault for the bridge, at the price of having to log the terminal in once by hand (RDP).

## Options

| # | Option | Cost | Fit |
|---|---|---|---|
| A | **Windows VM on OCI**, the existing Terraform | Compute for E5.Flex is US$ 0.039318 per OCPU-hour and US$ 0.0026212 per GB-hour (Oracle's price highlights page), about US$ 0.06 per hour for 1 OCPU / 8 GB, about US$ 44 a month running 24/7. **The Windows Server license is billed on top, per OCPU, and its rate is not verified here: check Oracle's cost estimator.** Not covered by Always Free. | Same private subnet, IAM, Vault and Bastion as the rest. Recommended. |
| B | Linux plus Wine on the free Ampere A1 | Free | Not recommended. Needs x86 emulation on Arm to run a trading terminal and its Python API. Unsupported and fragile where a hung terminal can mean an order in an unknown state. |
| C | The operator's own Windows PC | Free | Fine for tests (done), not for the deployment: the PC sleeps, the IP changes, and it is outside the private subnet and the IAM boundary. |
| D | A Windows VPS elsewhere, joined by a private tunnel | Varies | Possible, but moves credentials and the terminal outside the Oracle boundary the ADR relies on. |

## Recommendation

Option A, with these cost controls:

1. Size it as in the Terraform (1 OCPU / 8 GB) and watch the first bill.
2. B3 trades Monday to Friday, 10:00 to 17:55 in São Paulo. A standard shape stops billing compute
   and license while stopped (per Oracle's licensing note), so a scheduled stop outside the session
   would cut the bill by about two thirds. This needs a small scheduler on a Linux instance
   (instance principal, `oci compute instance action`) and the terminal must be logged in again
   after each start. Optional, after the first month.

## Work package (not done here)

Everything below touches the running Oracle stack and cannot be validated from a laptop, so it
lands as its own reviewed change, applied on a quiet day (each deploy resets the watchdog's 24 h
coverage):

1. `create_mt5 = true` explicitly, apply, then runbook step 7 (installer, demo login, Algotrading).
2. Run the bridge as a service on the VM (`cloud-init/mt5.ps1.tftpl`), bound to the private address,
   with `proto-mt5-bridge-token` from the Vault.
3. A second executor, `EXECUTOR_VENUE=XP_B3`, as another container on the executor instance (port
   8101, its own database, its own `EXECUTOR_API_TOKEN`), plus the Vault secrets, the instance
   principal's policy for them and `fetch-secrets.sh`/`proto-deploy.sh` support for the role.
   `proto-deploy.sh` also deploys the Binance executor: change it carefully and keep that path
   byte-identical.
4. On the API: `XP_EXECUTOR_URL`, `XP_EXECUTOR_API_TOKEN`, and the TradingView instrument list if
   TradingView will send B3 symbols.
5. Extend the independent watchdog to probe the XP executor before anything beyond the demo account.
6. Do not enable any of this until the loss-limit change (measure daily loss and drawdown against at
   most the reference capital) is merged: the demo account holds R$ 1,000,000.
