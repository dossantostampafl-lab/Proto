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

# "secret-family", not "secret-bundles": a read by name first looks the secret up, which needs the
# secret's metadata too. With secret-bundles alone every form returned 404 (NotAuthorizedOrNotFound).
# How each statement names the reader and the secret. Every mode keeps the per-secret restriction
# (an instance never reads another role's secrets); they differ only in the policy variables used.
# setup.sh tests them against the real tenancy and keeps the first that reads, in this order.
locals {
  secret_policy_templates = {
    dg_vault_name  = "Allow dynamic-group {dg} to read secret-family in compartment id {comp} where all {target.vault.id = '{vault}', target.secret.name = '{name}'}"
    dg_name        = "Allow dynamic-group {dg} to read secret-family in compartment id {comp} where target.secret.name = '{name}'"
    dg_id          = "Allow dynamic-group {dg} to read secret-family in compartment id {comp} where target.secret.id = '{id}'"
    principal_name = "Allow any-user to read secret-family in compartment id {comp} where all {request.principal.id = '{instance}', target.secret.name = '{name}'}"
    principal_id   = "Allow any-user to read secret-family in compartment id {comp} where all {request.principal.id = '{instance}', target.secret.id = '{id}'}"
  }
  secret_policy_by_id = endswith(var.secret_policy_mode, "_id")
}

# Secret OCIDs, looked up by name, only for the modes that condition on the id. The secrets are
# created outside Terraform (create-secrets.sh), so they must exist before such an apply.
data "oci_vault_secrets" "by_name" {
  for_each       = local.secret_policy_by_id ? toset(flatten(values(local.secrets))) : toset([])
  compartment_id = var.compartment_ocid
  vault_id       = oci_kms_vault.proto.id
  name           = each.key
}

resource "oci_identity_policy" "secrets" {
  provider       = oci.home
  for_each       = local.instance_ids
  compartment_id = var.compartment_ocid
  name           = "${local.name}-${each.key}-secrets"
  description    = "PROTO ${each.key}: read its own secrets in the PROTO vault, nothing else"
  freeform_tags  = local.tags
  # One flat statement per secret. A nested all { vault, any { names } } was accepted, but every
  # read under it returned 404.
  statements = [
    for s in local.secrets[each.key] : replace(replace(replace(replace(replace(replace(
      local.secret_policy_templates[var.secret_policy_mode],
      "{dg}", oci_identity_dynamic_group.role[each.key].name),
      "{comp}", var.compartment_ocid),
      "{vault}", oci_kms_vault.proto.id),
      "{name}", s),
      "{id}", local.secret_policy_by_id ? data.oci_vault_secrets.by_name[s].secrets[0].id : ""),
    "{instance}", each.value)
  ]
}
