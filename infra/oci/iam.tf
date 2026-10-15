# Instance principals: each instance is its own dynamic group and reads only its own secrets.
# No API keys or user credentials live on any instance.

locals {
  instance_ids = merge(
    { executor = local.executor_id, watchdog = local.watchdog_id },
    var.create_api ? { api = local.api_id } : {},
    var.create_mt5 ? { mt5 = oci_core_instance.mt5[0].id } : {},
  )
}

resource "oci_identity_dynamic_group" "role" {
  provider       = oci.home
  for_each       = local.instance_ids
  compartment_id = var.tenancy_ocid
  name           = "${local.name}-${each.key}"
  description    = "PROTO ${each.key} instance"
  matching_rule  = "ALL {instance.id = '${each.value}'}"
  freeform_tags  = local.tags
}

resource "oci_identity_policy" "secrets" {
  provider       = oci.home
  for_each       = local.instance_ids
  compartment_id = var.compartment_ocid
  name           = "${local.name}-${each.key}-secrets"
  description    = "PROTO ${each.key}: read its own secrets in the PROTO vault, nothing else"
  freeform_tags  = local.tags
  # One statement per secret, each a flat "all" of the vault and the name. A nested
  # all { ..., any { ... } } was accepted, but every read under it returned 404.
  statements = [
    for s in local.secrets[each.key] : format(
      "Allow dynamic-group %s to read secret-bundles in compartment id %s where all {target.vault.id = '%s', target.secret.name = '%s'}",
      oci_identity_dynamic_group.role[each.key].name,
      var.compartment_ocid,
      oci_kms_vault.proto.id,
      s,
    )
  ]
}
