# One NSG per role. Inbound is allow-listed by source NSG; nothing else gets in.
#
#   internet ──443──> api
#   api, watchdog ──8100──> executor          (intents + permits; watchdog: status, halt)
#   executor ──8200──> mt5                    (narrow MT5 RPC bridge)
#   bastion ──22──> linux instances, ──3389──> mt5
#
# Egress is open (stateful): the executor needs Binance over 443 through the NAT, and every
# instance needs package mirrors and Vault. Inbound is what this file restricts.

resource "oci_core_network_security_group" "api" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.proto.id
  display_name   = "${local.name}-api"
  freeform_tags  = local.tags
}

resource "oci_core_network_security_group" "executor" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.proto.id
  display_name   = "${local.name}-executor"
  freeform_tags  = local.tags
}

resource "oci_core_network_security_group" "watchdog" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.proto.id
  display_name   = "${local.name}-watchdog"
  freeform_tags  = local.tags
}

resource "oci_core_network_security_group" "mt5" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.proto.id
  display_name   = "${local.name}-mt5"
  freeform_tags  = local.tags
}

locals {
  nsgs = {
    api      = oci_core_network_security_group.api.id
    executor = oci_core_network_security_group.executor.id
    watchdog = oci_core_network_security_group.watchdog.id
    mt5      = oci_core_network_security_group.mt5.id
  }
}

resource "oci_core_network_security_group_security_rule" "egress" {
  for_each                  = local.nsgs
  network_security_group_id = each.value
  direction                 = "EGRESS"
  protocol                  = "all"
  destination               = "0.0.0.0/0"
  destination_type          = "CIDR_BLOCK"
  stateless                 = false
}

resource "oci_core_network_security_group_security_rule" "api_https" {
  network_security_group_id = oci_core_network_security_group.api.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = "0.0.0.0/0"
  source_type               = "CIDR_BLOCK"
  description               = "Dashboard, API and TradingView webhooks (TLS)"

  tcp_options {
    destination_port_range {
      min = 443
      max = 443
    }
  }
}

# The executor's private API: only the public API and the watchdog reach it.
resource "oci_core_network_security_group_security_rule" "executor_from" {
  for_each                  = { api = oci_core_network_security_group.api.id, watchdog = oci_core_network_security_group.watchdog.id }
  network_security_group_id = oci_core_network_security_group.executor.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = each.value
  source_type               = "NETWORK_SECURITY_GROUP"
  description               = "Executor API from ${each.key}"

  tcp_options {
    destination_port_range {
      min = var.executor_port
      max = var.executor_port
    }
  }
}

# The Alpaca (US equities, PAPER) executor on the same instance, its own port. Only with
# enable_alpaca (default false): without it the NSG is exactly as before.
resource "oci_core_network_security_group_security_rule" "executor_alpaca_from" {
  for_each                  = var.enable_alpaca ? { api = oci_core_network_security_group.api.id, watchdog = oci_core_network_security_group.watchdog.id } : {}
  network_security_group_id = oci_core_network_security_group.executor.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = each.value
  source_type               = "NETWORK_SECURITY_GROUP"
  description               = "Alpaca executor API from ${each.key}"

  tcp_options {
    destination_port_range {
      min = var.alpaca_executor_port
      max = var.alpaca_executor_port
    }
  }
}

# The public API is reached by the watchdog too, over the VCN (its private address).
resource "oci_core_network_security_group_security_rule" "api_from_watchdog" {
  network_security_group_id = oci_core_network_security_group.api.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = oci_core_network_security_group.watchdog.id
  source_type               = "NETWORK_SECURITY_GROUP"
  description               = "Watchdog heartbeats and halts to the API"

  tcp_options {
    destination_port_range {
      min = 8000
      max = 8000
    }
  }
}

# Only the executor talks to the MT5 bridge.
resource "oci_core_network_security_group_security_rule" "mt5_bridge" {
  network_security_group_id = oci_core_network_security_group.mt5.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = oci_core_network_security_group.executor.id
  source_type               = "NETWORK_SECURITY_GROUP"
  description               = "MT5 RPC bridge from the executor"

  tcp_options {
    destination_port_range {
      min = var.mt5_bridge_port
      max = var.mt5_bridge_port
    }
  }
}

# Bastion sessions arrive from inside the private subnet.
resource "oci_core_network_security_group_security_rule" "ssh_from_bastion" {
  for_each                  = { api = oci_core_network_security_group.api.id, executor = oci_core_network_security_group.executor.id, watchdog = oci_core_network_security_group.watchdog.id }
  network_security_group_id = each.value
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = var.private_subnet_cidr
  source_type               = "CIDR_BLOCK"
  description               = "SSH through the Bastion"

  tcp_options {
    destination_port_range {
      min = 22
      max = 22
    }
  }
}

resource "oci_core_network_security_group_security_rule" "rdp_from_bastion" {
  network_security_group_id = oci_core_network_security_group.mt5.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = var.private_subnet_cidr
  source_type               = "CIDR_BLOCK"
  description               = "RDP through the Bastion"

  tcp_options {
    destination_port_range {
      min = 3389
      max = 3389
    }
  }
}
