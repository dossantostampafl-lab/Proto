data "oci_identity_availability_domains" "ads" {
  compartment_id = var.tenancy_ocid
}

data "oci_core_images" "oracle_linux_arm" {
  compartment_id           = var.compartment_ocid
  operating_system         = "Oracle Linux"
  operating_system_version = "9"
  shape                    = var.a1_shape
  sort_by                  = "TIMECREATED"
  sort_order               = "DESC"
}

data "oci_core_images" "watchdog" {
  compartment_id           = var.compartment_ocid
  operating_system         = "Oracle Linux"
  operating_system_version = "9"
  shape                    = var.watchdog_shape
  sort_by                  = "TIMECREATED"
  sort_order               = "DESC"
}

data "oci_core_images" "windows" {
  count                    = var.create_mt5 ? 1 : 0
  compartment_id           = var.compartment_ocid
  operating_system         = "Windows"
  operating_system_version = var.mt5_windows_version
  shape                    = var.mt5_shape
  sort_by                  = "TIMECREATED"
  sort_order               = "DESC"
}

locals {
  ad           = data.oci_identity_availability_domains.ads.availability_domains[0].name
  linux_image  = data.oci_core_images.oracle_linux_arm.images[0].id
  fetch_script = base64encode(file("${path.module}/scripts/fetch-secrets.sh"))

  cloud_init = {
    for role, ports in {
      api      = [443, 8000]
      executor = [var.executor_port]
      watchdog = []
      } : role => base64encode(templatefile("${path.module}/cloud-init/linux.yaml.tftpl", {
        role             = role
        vault_id         = oci_kms_vault.proto.id
        fetch_script_b64 = local.fetch_script
        open_ports       = ports
    }))
  }
}

# Public API + dashboard. Only 443 is open to the internet (nsg.tf); TLS terminates on it.
resource "oci_core_instance" "api" {
  count               = var.create_api ? 1 : 0
  compartment_id      = var.compartment_ocid
  availability_domain = local.ad
  display_name        = "${local.name}-api"
  shape               = var.a1_shape
  freeform_tags       = local.tags

  shape_config {
    ocpus         = var.api_ocpus
    memory_in_gbs = var.api_memory_gb
  }

  source_details {
    source_type = "image"
    source_id   = local.linux_image
  }

  create_vnic_details {
    subnet_id        = oci_core_subnet.public.id
    assign_public_ip = true
    nsg_ids          = [oci_core_network_security_group.api.id]
    hostname_label   = "api"
  }

  metadata = {
    ssh_authorized_keys = var.ssh_public_key
    user_data           = local.cloud_init["api"]
  }

  agent_config {
    plugins_config {
      name          = "Bastion"
      desired_state = "ENABLED"
    }
  }
}

# The only process with venue credentials. No public IP, ever.
resource "oci_core_instance" "executor" {
  compartment_id      = var.compartment_ocid
  availability_domain = local.ad
  display_name        = "${local.name}-executor"
  shape               = var.a1_shape
  freeform_tags       = local.tags

  shape_config {
    ocpus         = var.executor_ocpus
    memory_in_gbs = var.executor_memory_gb
  }

  source_details {
    source_type = "image"
    source_id   = local.linux_image
  }

  create_vnic_details {
    subnet_id        = oci_core_subnet.private.id
    assign_public_ip = false
    private_ip       = var.executor_private_ip
    nsg_ids          = [oci_core_network_security_group.executor.id]
    hostname_label   = "executor"
  }

  metadata = {
    ssh_authorized_keys = var.ssh_public_key
    user_data           = local.cloud_init["executor"]
  }

  agent_config {
    plugins_config {
      name          = "Bastion"
      desired_state = "ENABLED"
    }
  }
}

# Independent watchdog: its own instance, halts only (docs/watchdog/watchdog.md).
resource "oci_core_instance" "watchdog" {
  compartment_id      = var.compartment_ocid
  availability_domain = local.ad
  display_name        = "${local.name}-watchdog"
  shape               = var.watchdog_shape
  freeform_tags       = local.tags

  # Fixed shapes (the free micro) take no shape_config; flex shapes need one.
  dynamic "shape_config" {
    for_each = endswith(var.watchdog_shape, ".Flex") ? [1] : []
    content {
      ocpus         = var.watchdog_ocpus
      memory_in_gbs = var.watchdog_memory_gb
    }
  }

  source_details {
    source_type = "image"
    source_id   = data.oci_core_images.watchdog.images[0].id
  }

  create_vnic_details {
    subnet_id        = oci_core_subnet.private.id
    assign_public_ip = false
    private_ip       = var.watchdog_private_ip
    nsg_ids          = [oci_core_network_security_group.watchdog.id]
    hostname_label   = "watchdog"
  }

  metadata = {
    ssh_authorized_keys = var.ssh_public_key
    user_data           = local.cloud_init["watchdog"]
  }

  agent_config {
    plugins_config {
      name          = "Bastion"
      desired_state = "ENABLED"
    }
  }
}

# XP MetaTrader 5 terminal. Windows on x86; the account password stays on this VM.
resource "oci_core_instance" "mt5" {
  count               = var.create_mt5 ? 1 : 0
  compartment_id      = var.compartment_ocid
  availability_domain = local.ad
  display_name        = "${local.name}-mt5"
  shape               = var.mt5_shape
  freeform_tags       = local.tags

  shape_config {
    ocpus         = var.mt5_ocpus
    memory_in_gbs = var.mt5_memory_gb
  }

  source_details {
    source_type = "image"
    source_id   = data.oci_core_images.windows[0].images[0].id
  }

  create_vnic_details {
    subnet_id        = oci_core_subnet.private.id
    assign_public_ip = false
    private_ip       = var.mt5_private_ip
    nsg_ids          = [oci_core_network_security_group.mt5.id]
    hostname_label   = "mt5"
  }

  # RDP goes through a Bastion port-forwarding session, which needs no agent plugin.
  metadata = {
    user_data = base64encode(templatefile("${path.module}/cloud-init/mt5.ps1.tftpl", {
      bridge_port = var.mt5_bridge_port
      executor_ip = var.executor_private_ip
    }))
  }
}
