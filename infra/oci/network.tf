# One VCN, two subnets:
#   public  (10.20.1.0/24): the API, the only instance reachable from the internet (443).
#   private (10.20.2.0/24): executor, watchdog and the MT5 Windows VM. No public IPs; egress
#                           through the NAT gateway, whose reserved IP is the one Binance
#                           allowlists on the API key.

locals {
  name = "proto"
  tags = { "project" = "proto" }
}

resource "oci_core_vcn" "proto" {
  compartment_id = var.compartment_ocid
  cidr_blocks    = [var.vcn_cidr]
  display_name   = "${local.name}-vcn"
  dns_label      = "proto"
  freeform_tags  = local.tags
}

resource "oci_core_internet_gateway" "igw" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.proto.id
  display_name   = "${local.name}-igw"
  enabled        = true
  freeform_tags  = local.tags
}

# Reserved, so the egress IP survives a NAT gateway rebuild and the Binance allowlist holds.
resource "oci_core_public_ip" "nat" {
  compartment_id = var.compartment_ocid
  lifetime       = "RESERVED"
  display_name   = "${local.name}-nat-egress"
  freeform_tags  = local.tags

  lifecycle {
    prevent_destroy = true
  }
}

resource "oci_core_nat_gateway" "nat" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.proto.id
  display_name   = "${local.name}-nat"
  public_ip_id   = oci_core_public_ip.nat.id
  freeform_tags  = local.tags
}

data "oci_core_services" "all" {
  filter {
    name   = "name"
    values = ["All .* Services In Oracle Services Network"]
    regex  = true
  }
}

# Vault, Object Storage and the OS package mirrors without leaving Oracle's network.
resource "oci_core_service_gateway" "services" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.proto.id
  display_name   = "${local.name}-services"
  freeform_tags  = local.tags

  services {
    service_id = data.oci_core_services.all.services[0].id
  }
}

resource "oci_core_route_table" "public" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.proto.id
  display_name   = "${local.name}-public-rt"
  freeform_tags  = local.tags

  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_internet_gateway.igw.id
  }
}

resource "oci_core_route_table" "private" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.proto.id
  display_name   = "${local.name}-private-rt"
  freeform_tags  = local.tags

  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_nat_gateway.nat.id
  }

  route_rules {
    destination       = data.oci_core_services.all.services[0].cidr_block
    destination_type  = "SERVICE_CIDR_BLOCK"
    network_entity_id = oci_core_service_gateway.services.id
  }
}

# Security lists stay empty: every rule lives in the per-role NSGs (nsg.tf).
resource "oci_core_security_list" "empty" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.proto.id
  display_name   = "${local.name}-no-rules"
  freeform_tags  = local.tags
}

# The Bastion's private endpoint sits in the private subnet with no NSG, so only the subnet's
# security list governs its traffic. An empty list blocks its egress and every session closes
# right after authentication. Egress only, to SSH and RDP inside the VCN: inbound stays NSG-only.
resource "oci_core_security_list" "bastion_egress" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.proto.id
  display_name   = "${local.name}-bastion-egress"
  freeform_tags  = local.tags

  dynamic "egress_security_rules" {
    for_each = { ssh = 22, rdp = 3389 }
    content {
      protocol         = "6"
      destination      = var.vcn_cidr
      destination_type = "CIDR_BLOCK"
      stateless        = false
      description      = "Bastion ${egress_security_rules.key} to the instances"
      tcp_options {
        min = egress_security_rules.value
        max = egress_security_rules.value
      }
    }
  }
}

resource "oci_core_subnet" "public" {
  compartment_id    = var.compartment_ocid
  vcn_id            = oci_core_vcn.proto.id
  cidr_block        = var.public_subnet_cidr
  display_name      = "${local.name}-public"
  dns_label         = "pub"
  route_table_id    = oci_core_route_table.public.id
  security_list_ids = [oci_core_security_list.empty.id]
  freeform_tags     = local.tags
}

resource "oci_core_subnet" "private" {
  compartment_id             = var.compartment_ocid
  vcn_id                     = oci_core_vcn.proto.id
  cidr_block                 = var.private_subnet_cidr
  display_name               = "${local.name}-private"
  dns_label                  = "priv"
  route_table_id             = oci_core_route_table.private.id
  security_list_ids          = [oci_core_security_list.bastion_egress.id]
  prohibit_public_ip_on_vnic = true
  freeform_tags              = local.tags
}

# Operator access to the private instances (SSH, RDP) goes through the managed Bastion only.
resource "oci_bastion_bastion" "operator" {
  bastion_type                 = "STANDARD"
  compartment_id               = var.compartment_ocid
  target_subnet_id             = oci_core_subnet.private.id
  name                         = "${local.name}bastion"
  client_cidr_block_allow_list = var.operator_cidrs
  max_session_ttl_in_seconds   = 10800
  freeform_tags                = local.tags
}
