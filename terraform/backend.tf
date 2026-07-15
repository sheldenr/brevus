# Terraform Backend Configuration for Remote State Locking
terraform {
  backend "azurerm" {
    resource_group_name  = "rg-brevus-tfstate"
    storage_account_name = "stbrevusstatefile"
    container_name       = "tfstate"
    key                  = "development.terraform.tfstate"
  }
}
