# PROTO on Oracle Cloud (OCI): the layout of docs/adr/venues-and-executor-hosting.md.
# Apply from OCI Cloud Shell (already authenticated): docs/runbooks/oracle-setup.md.

terraform {
  required_version = ">= 1.5"
  required_providers {
    oci = {
      source  = "oracle/oci"
      version = "~> 6.0"
    }
  }
}

# Cloud Shell authenticates the provider. Elsewhere, use ~/.oci/config.
provider "oci" {
  region = var.region
}

# IAM (dynamic groups and policies) is written in the tenancy's home region.
provider "oci" {
  alias  = "home"
  region = var.home_region
}
