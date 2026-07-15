# Terraform Infrastructure Blueprint for Brevus Ephemeral Storage

resource "random_id" "unique_suffix" {
  byte_length = 4
}

# 1. Resource Group
resource "azurerm_resource_group" "rg" {
  name     = "rg-${var.project_name}-${var.environment}"
  location = var.location
  tags = {
    Environment = var.environment
    Project     = var.project_name
    ManagedBy   = "Terraform"
  }
}

# 2. Azure User-Assigned Managed Identity (Least Privilege Identity Federation)
resource "azurerm_user_assigned_identity" "func_identity" {
  name                = "id-func-${var.project_name}-${var.environment}"
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
}

# 3. Azure Storage Account (Secure Private Blob Storage Container)
resource "azurerm_storage_account" "ephemeral_store" {
  name                             = "st${var.project_name}store${random_id.unique_suffix.hex}"
  resource_group_name              = azurerm_resource_group.rg.name
  location                         = azurerm_resource_group.rg.location
  account_tier                     = "Standard"
  account_replication_type         = "LRS"
  enable_https_traffic_only        = true
  min_tls_version                  = "TLS1_2"
  public_network_access_enabled    = false # Disable public network access for security
  infrastructure_encryption_enabled = true   # Double encryption at rest for enterprise compliance

  network_rules {
    default_action             = "Deny"
    bypass                     = ["AzureServices"]
  }

  tags = {
    Environment = var.environment
    Purpose     = "Private Blob Ingestion"
  }
}

# Private Blob Container
resource "azurerm_storage_container" "ephemeral_container" {
  name                  = "ephemeral-files"
  storage_account_name  = azurerm_storage_account.ephemeral_store.name
  container_access_type = "private"
}

# 4. Azure Cosmos DB Account (NoSQL API, Serverless Mode)
resource "azurerm_cosmosdb_account" "db_account" {
  name                = "cosmos-${var.project_name}-${var.environment}-${random_id.unique_suffix.hex}"
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
  offer_type          = "Standard"
  kind                = "GlobalDocumentDB"

  consistency_policy {
    consistency_level = "Session"
  }

  geo_location {
    location          = azurerm_resource_group.rg.location
    failover_priority = 0
  }

  capabilities {
    name = "EnableServerless"
  }

  # All versions and deletes change feed mode requires continuous backups
  backup {
    type = "Continuous"
  }

  tags = {
    Environment = var.environment
  }
}

# Cosmos DB Database
resource "azurerm_cosmosdb_sql_database" "db" {
  name                = "EphemeralDb"
  resource_group_name = azurerm_resource_group.rg.name
  account_name        = azurerm_cosmosdb_account.db_account.name
}

# Cosmos DB Container (with Default TTL Enabled but no automatic sweep until doc specifies it)
resource "azurerm_cosmosdb_sql_container" "container" {
  name                  = "FileMetadata"
  resource_group_name   = azurerm_resource_group.rg.name
  account_name          = azurerm_cosmosdb_account.db_account.name
  database_name         = azurerm_cosmosdb_sql_database.db.name
  partition_key_paths   = ["/filePath"]
  default_ttl           = -1 # TTL is enabled, but items won't expire unless custom ttl property is set

  # Enable Change Feed Log Store / All Versions and Deletes mode (Requires continuous backup)
  # In Terraform, enabling the Preview feature of All Versions & Deletes is done by setting the database/container policies
  # or enabling it through continuous backup and account configuration
}

# 5. Role Assignments (Using Managed Identity & RBAC instead of Connection Strings)

# Storage Blob Data Contributor Role
resource "azurerm_role_assignment" "blob_contributor" {
  scope                = azurerm_storage_account.ephemeral_store.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id          = azurerm_user_assigned_identity.func_identity.principal_id
}

# Cosmos DB Built-in Data Contributor (Data Plane access via azurerm_cosmosdb_sql_role_assignment)
resource "azurerm_cosmosdb_sql_role_assignment" "cosmos_data_contributor" {
  resource_group_name = azurerm_resource_group.rg.name
  account_name        = azurerm_cosmosdb_account.db_account.name
  principal_id        = azurerm_user_assigned_identity.func_identity.principal_id
  scope               = azurerm_cosmosdb_account.db_account.id
  role_definition_id  = "${azurerm_cosmosdb_account.db_account.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002"
}

# 6. Azure Function App Infrastructure (Hosting & Compute)

# Dedicated storage account for Function App internal state and logs
resource "azurerm_storage_account" "func_state_store" {
  name                     = "stfuncstate${random_id.unique_suffix.hex}"
  resource_group_name      = azurerm_resource_group.rg.name
  location                 = azurerm_resource_group.rg.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
}

# App Service Plan (Consumption plan for serverless scaling)
resource "azurerm_service_plan" "asp" {
  name                = "asp-${var.project_name}-${var.environment}"
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
  os_type             = "Linux"
  sku_name            = "Y1"
}

# Log Analytics & App Insights
resource "azurerm_log_analytics_workspace" "law" {
  name                = "law-${var.project_name}-${var.environment}"
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
  sku                 = "PerGB2018"
}

resource "azurerm_application_insights" "app_insights" {
  name                = "appi-${var.project_name}-${var.environment}"
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
  workspace_id        = azurerm_log_analytics_workspace.law.id
  application_type    = "web"
}

# Linux Function App
resource "azurerm_linux_function_app" "function_app" {
  name                = "func-${var.project_name}-${var.environment}-${random_id.unique_suffix.hex}"
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location

  service_plan_id            = azurerm_service_plan.asp.id
  storage_account_name       = azurerm_storage_account.func_state_store.name
  storage_account_access_key = azurerm_storage_account.func_state_store.primary_access_key

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.func_identity.id]
  }

  site_config {
    application_stack {
      python_version = "3.10"
    }
  }

  app_settings = {
    "FUNCTIONS_WORKER_RUNTIME"       = "python"
    "AzureWebJobsFeatureFlags"       = "EnableWorkerIndexing"
    "APPINSIGHTS_INSTRUMENTATIONKEY" = azurerm_application_insights.app_insights.instrumentation_key
    
    # Secure Identity-Based Connections for SDKs and Bindings (No Connection Strings stored in Config!)
    "BlobStorageConnectionString__accountName" = azurerm_storage_account.ephemeral_store.name
    "BlobContainerName"                        = azurerm_storage_container.ephemeral_container.name
    
    "CosmosDBConnectionString__accountEndpoint" = azurerm_cosmosdb_account.db_account.endpoint
    "CosmosDBConnectionString__credential"      = "managedidentity"
    "CosmosDBConnectionString__clientId"        = azurerm_user_assigned_identity.func_identity.client_id
    
    "CosmosDBDatabaseName"                     = azurerm_cosmosdb_sql_database.db.name
    "CosmosDBContainerName"                    = azurerm_cosmosdb_sql_container.container.name
    
    # Email alert notifications (using dummy placeholder; to be configured post-provisioning)
    "AzureCommunicationServicesEmailConnectionString" = "endpoint=https://dummy.communication.azure.com/;accesskey=dummykey"
    "EmailSenderAddress"                              = var.notification_email_sender
    "EmailRecipientAddress"                            = var.notification_email_recipient
  }
}
