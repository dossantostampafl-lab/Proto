# infra/oci

Terraform for PROTO on Oracle Cloud: the layout of `docs/adr/venues-and-executor-hosting.md`.
It creates the VCN, the NAT with a reserved egress IP, the per-role NSGs, a Bastion, the Vault
and its least-privilege IAM, and the instances: API, executor, watchdog and the MT5 Windows VM.

Follow `docs/runbooks/oracle-setup.md`. Terraform never writes a secret value: secrets are created
directly in the Vault, and each instance reads only its own at boot (`scripts/fetch-secrets.sh`).

| File | |
|---|---|
| `network.tf` | VCN, subnets, gateways, routes, Bastion |
| `nsg.tf` | Inbound allow-list per role |
| `vault.tf` | Vault, key, secret names per role |
| `iam.tf` | Dynamic groups and secret-read policies (home region) |
| `compute.tf` | Instances and cloud-init |
| `cloud-init/`, `scripts/` | Boot configuration and the Vault fetch |
