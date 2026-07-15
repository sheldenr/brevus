# Terraform Providers Configuration
terraform {
  required_version = ">= 1.5.0"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.0"
    }
  }
}

provider "azurerm" {
  features {
    resource_group {
      prevent_deletion_if_contains_resources = false
    }
    cosmosdb {
      # Prevents resource locks from blocking teardown of cosmos during dev testing
      key_vault_key_rotation_enabled_for_db_accounts = false
    }
  }
}
