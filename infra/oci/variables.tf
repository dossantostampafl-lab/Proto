variable "tenancy_ocid" {
  description = "Tenancy OCID. In Cloud Shell: echo $OCI_TENANCY"
  type        = string
}

variable "compartment_ocid" {
  description = "Compartment for every PROTO resource. A dedicated one keeps the IAM policies narrow."
  type        = string
}

variable "region" {
  description = "Region for the network and the instances. Always Free resources exist only in the home region."
  type        = string
  default     = "sa-valparaiso-1"
}

variable "home_region" {
  description = "Tenancy home region, where IAM is written. Console: Governance > Tenancy details."
  type        = string
  default     = "sa-valparaiso-1"
}

variable "ssh_public_key" {
  description = "Operator SSH public key for the Linux instances (reached through the Bastion only)."
  type        = string
}

variable "operator_cidrs" {
  description = "CIDRs allowed to connect to the Bastion. From Cloud Shell use [\"0.0.0.0/0\"]: it reaches the Bastion from inside Oracle, not from its ifconfig.me address."
  type        = list(string)
}

variable "vcn_cidr" {
  type    = string
  default = "10.20.0.0/16"
}

variable "public_subnet_cidr" {
  type    = string
  default = "10.20.1.0/24"
}

variable "private_subnet_cidr" {
  type    = string
  default = "10.20.2.0/24"
}

variable "create_api" {
  description = "Create the public API instance. Set false when the API already runs elsewhere."
  type        = bool
  default     = true
}

variable "create_mt5" {
  description = "Create the Windows VM for the XP MetaTrader 5 terminal (billed: Windows needs an x86 shape and a license)."
  type        = bool
  default     = true
}

# Ampere A1 (Arm). The free tier covers 4 OCPUs and 24 GB in total across these.
variable "a1_shape" {
  type    = string
  default = "VM.Standard.A1.Flex"
}

variable "api_ocpus" {
  type    = number
  default = 1
}

variable "api_memory_gb" {
  type    = number
  default = 4
}

variable "executor_ocpus" {
  type    = number
  default = 1
}

variable "executor_memory_gb" {
  type    = number
  default = 4
}

# Arm by default: some home regions (Chile West, Valparaiso) have no Always Free AMD micro shape.
# Where VM.Standard.E2.1.Micro exists, it frees an Arm OCPU for something else.
variable "watchdog_shape" {
  type    = string
  default = "VM.Standard.A1.Flex"
}

variable "watchdog_ocpus" {
  type    = number
  default = 1
}

variable "watchdog_memory_gb" {
  type    = number
  default = 4
}

# MT5 runs only on Windows, and Windows only on x86.
variable "mt5_shape" {
  type    = string
  default = "VM.Standard.E5.Flex"
}

variable "mt5_ocpus" {
  type    = number
  default = 1
}

variable "mt5_memory_gb" {
  type    = number
  default = 8
}

variable "mt5_windows_version" {
  type    = string
  default = "Server 2022 Standard"
}

# Fixed private addresses, so the API, the watchdog and the docs can name them.
variable "executor_private_ip" {
  type    = string
  default = "10.20.2.10"
}

variable "watchdog_private_ip" {
  type    = string
  default = "10.20.2.11"
}

variable "mt5_private_ip" {
  type    = string
  default = "10.20.2.20"
}

variable "executor_port" {
  type    = number
  default = 8100
}

# Alpaca US equities, PAPER account only (docs/venues/alpaca-paper.md): opens the Alpaca
# executor's port to the API and the watchdog. Off by default: nothing changes.
variable "enable_alpaca" {
  type    = bool
  default = false
}

variable "alpaca_executor_port" {
  type    = number
  default = 8101
}

variable "mt5_bridge_port" {
  description = "Port of the narrow MT5 RPC bridge on the Windows VM (submit, cancel, query, positions, balances)."
  type        = number
  default     = 8200
}

# Optional placement. "Out of host capacity" on Always Free Arm often means Oracle looked on full
# hosts; pinning the fault domain of an instance that just shrank (for example The Creation) lands
# the new ones where capacity was freed. null lets Oracle choose.
variable "fault_domain" {
  type    = string
  default = null
}

# Instances created by hand are adopted by OCID (Compute > Instances > the instance > OCID).
# When set, Terraform does not create that instance; it only wires its IAM and outputs.
variable "executor_instance_id" {
  type    = string
  default = null
}

variable "watchdog_instance_id" {
  type    = string
  default = null
}

variable "api_instance_id" {
  type    = string
  default = null
}

# Policy variables used to restrict each instance to its own secrets (iam.tf). setup.sh picks the
# first mode that the tenancy honours; all of them keep the per-secret restriction.
variable "secret_policy_mode" {
  type    = string
  default = "dg_name"
  validation {
    condition     = contains(["dg_name", "dg_id", "principal_name", "principal_id"], var.secret_policy_mode)
    error_message = "secret_policy_mode: dg_name, dg_id, principal_name or principal_id."
  }
}
