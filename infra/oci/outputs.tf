output "nat_egress_ip" {
  description = "Restrict the Binance API key to this IP (Binance > API Management > IP access restriction)."
  value       = oci_core_public_ip.nat.ip_address
}

output "api_public_ip" {
  description = "Point the dashboard's DNS name here; only 443 is open."
  value       = var.create_api ? oci_core_instance.api[0].public_ip : null
}

output "private_ips" {
  value = {
    api      = var.create_api ? oci_core_instance.api[0].private_ip : null
    executor = oci_core_instance.executor.private_ip
    watchdog = oci_core_instance.watchdog.private_ip
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
