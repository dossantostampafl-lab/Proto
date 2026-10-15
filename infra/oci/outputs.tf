output "nat_egress_ip" {
  description = "Restrict the Binance API key to this IP (Binance > API Management > IP access restriction)."
  value       = oci_core_public_ip.nat.ip_address
}

output "api_public_ip" {
  description = "Point the dashboard's DNS name here; only 443 is open."
  value       = var.create_api ? data.oci_core_vnic.primary["api"].public_ip_address : null
}

output "private_ips" {
  value = {
    api      = var.create_api ? data.oci_core_vnic.primary["api"].private_ip_address : null
    executor = data.oci_core_vnic.primary["executor"].private_ip_address
    watchdog = data.oci_core_vnic.primary["watchdog"].private_ip_address
    mt5      = var.create_mt5 ? oci_core_instance.mt5[0].private_ip : null
  }
}

output "instance_ids" {
  value = local.instance_ids
}

output "bastion_id" {
  value = oci_bastion_bastion.operator.id
}

output "vault_id" {
  value = oci_kms_vault.proto.id
}

output "secrets_key_id" {
  description = "Encryption key for every secret created in the vault."
  value       = oci_kms_key.secrets.id
}

output "secret_names" {
  description = "Create each of these in the vault (docs/runbooks/oracle-setup.md)."
  value       = sort(distinct(flatten(values(local.secrets))))
}

# Addresses read from each instance's primary VNIC, whether Terraform created it or adopted it.
data "oci_core_vnic_attachments" "primary" {
  for_each       = { for k, v in local.instance_ids : k => v if k != "mt5" }
  compartment_id = var.compartment_ocid
  instance_id    = each.value
}

data "oci_core_vnic" "primary" {
  for_each = data.oci_core_vnic_attachments.primary
  vnic_id  = each.value.vnic_attachments[0].vnic_id
}
