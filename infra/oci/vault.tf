# OCI Vault holds every secret. Terraform creates the vault, the key and the access rules,
# never a secret value: values would land in the Terraform state. The operator creates each
# secret by name (docs/runbooks/oracle-setup.md), and each instance can read only its own.

resource "oci_kms_vault" "proto" {
  compartment_id = var.compartment_ocid
  display_name   = "${local.name}-vault"
  vault_type     = "DEFAULT"
  freeform_tags  = local.tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "oci_kms_key" "secrets" {
  compartment_id      = var.compartment_ocid
  display_name        = "${local.name}-secrets"
  management_endpoint = oci_kms_vault.proto.management_endpoint
  protection_mode     = "SOFTWARE"
  freeform_tags       = local.tags

  key_shape {
    algorithm = "AES"
    length    = 32
  }

  lifecycle {
    prevent_destroy = true
  }
}

locals {
  # Secret names per role. The instance fetches exactly these at boot (scripts/fetch-secrets.sh).
  # A token shared by two roles is one secret both may read: it never exists in two copies.
  secrets = {
    api = [
      "proto-operator-token",     # OPERATOR_API_TOKEN
      "proto-api-database-url",   # DATABASE_URL
      "proto-executor-token",     # EXECUTOR_API_TOKEN (API -> executor)
      "proto-watchdog-api-token", # WATCHDOG_API_TOKEN (watchdog -> API)
      "proto-permit-private-key", # RISK_PERMIT_PRIVATE_KEY (Ed25519 seed)
      "proto-tradingview-secret", # TRADINGVIEW_WEBHOOK_SECRET
    ]
    executor = [
      "proto-executor-token",          # EXECUTOR_API_TOKEN
      "proto-watchdog-executor-token", # EXECUTOR_WATCHDOG_TOKEN
      "proto-permit-public-key",       # EXECUTOR_PERMIT_PUBLIC_KEY
      "proto-executor-database-url",   # EXECUTOR_DATABASE_URL
      "proto-binance-testnet-key",     # PROTO_CRED_BINANCE_SPOT_TESTNET_MAIN_API_KEY
      "proto-binance-testnet-secret",  # PROTO_CRED_BINANCE_SPOT_TESTNET_MAIN_API_SECRET
      "proto-mt5-bridge-token",        # executor -> MT5 bridge
    ]
    watchdog = [
      "proto-watchdog-api-token",      # WATCHDOG_API_TOKEN
      "proto-watchdog-executor-token", # WATCHDOG_EXECUTOR_TOKEN
    ]
    mt5 = [
      "proto-mt5-bridge-token",
    ]
  }
}
