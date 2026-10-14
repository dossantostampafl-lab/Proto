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
  description = "Public CIDRs the operator connects from, allowed to open Bastion sessions (SSH, RDP)."
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

variable "mt5_bridge_port" {
  description = "Port of the narrow MT5 RPC bridge on the Windows VM (submit, cancel, query, positions, balances)."
  type        = number
  default     = 8200
}
