# Terraform Outputs

output "resource_group_name" {
  value       = azurerm_resource_group.rg.name
  description = "The name of the resource group."
}

output "function_app_name" {
  value       = azurerm_linux_function_app.function_app.name
  description = "The name of the Azure Function App."
}

output "function_app_default_hostname" {
  value       = azurerm_linux_function_app.function_app.default_hostname
  description = "The default hostname of the Azure Function App."
}

output "storage_account_name" {
  value       = azurerm_storage_account.ephemeral_store.name
  description = "The name of the Ephemeral Storage Account."
}

output "cosmos_db_endpoint" {
  value       = azurerm_cosmosdb_account.db_account.endpoint
  description = "The endpoint of the Cosmos DB Account."
}

output "user_assigned_identity_id" {
  value       = azurerm_user_assigned_identity.func_identity.id
  description = "The resource ID of the User-Assigned Managed Identity."
}
