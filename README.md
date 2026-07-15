# Brevus: Azure Serverless Data Governance & Ephemeral Storage Ingestion

Brevus is an enterprise-grade cloud-native architecture pattern on **Microsoft Azure** designed for secure, serverless file ingestion with automatic data retention enforcement. 

Rather than bottlenecking the application with a proxy server for file uploads, this solution enables clients to securely negotiate direct-to-storage upload channels and governs metadata lifecycles through Azure Cosmos DB Time-to-Live (TTL) and the Change Feed.

---

## 🏗️ Architectural Flow

```
                  [ 1. Request Upload URL (GetUploadUrl) ]
  Frontend  --------------------------------------------------->  Azure API Management / Function (SAS Generator)
            <---------------------------------------------------
                  [ 2. Returns SAS Token / URL & ID ]
      │
      └───────────[ 3. Uploads File Directly via HTTPS PUT ] ──────────> [ Azure Blob Storage (Private) ]
                                                                                │
                                                                       [ 4. Event Grid / Change Feed Trigger ]
                                                                                ▼
  User  <──────── [ 6. Alert via Logic Apps / Mail (ACS) ] <─────────── Azure Function (Cosmos DB Change Feed)
                                                                                └─[ 5. Deletes Expired Blob ]
```

1. **Upload Request**: The client requests a write-only upload URL from `GetUploadUrl`. The function generates a unique storage path and a Shared Access Signature (SAS) token valid for 5 minutes.
2. **Direct Ingestion**: The client uploads chunks directly to Azure Blob Storage via HTTPS PUT using the SAS URL, bypassing the compute backend.
3. **Metadata Logging**: The client logs metadata to `CreateFileMetadata` in Azure Cosmos DB, specifying a custom `ttl` property.
4. **Automated Expiry**: Cosmos DB’s internal lifecycle engine automatically deletes the metadata record once its TTL expires.
5. **Change Feed Cleanup**: An Azure Function triggered by the Cosmos DB Change Feed (configured in `"All versions and deletes"` mode) captures the deletion event, extracts the file path, and issues a delete command to Azure Blob Storage.
6. **Data Scrub Notification**: The cleanup function dispatches a confirmation email confirming the data scrub via Azure Communication Services (ACS) Email SDK.

---

## 📂 Repository Structure

The project is structured as follows:

```bash
├── .github/
│   └── workflows/
│       └── deploy.yml          # Enterprise CI/CD Pipeline (Checkov & Azure OIDC Federation)
├── functions/
│   ├── function_app.py        # Python Azure Functions (v2 Programming Model)
│   ├── host.json              # Azure Functions Global Settings
│   ├── local.settings.json    # Local Environment Variables & Secrets
│   └── requirements.txt       # Python Dependencies
├── terraform/
│   ├── backend.tf             # Remote State Backend Configuration (with locking)
│   ├── providers.tf           # Terraform Provider Definitions
│   ├── variables.tf           # Terraform Input Variables
│   ├── main.tf                # Blueprint (Cosmos DB, Storage, Managed Identity, RBAC)
│   └── outputs.tf             # Outputs (Endpoints, Names)
├── .gitignore                 # Excludes local configs and storage folders from Git
├── simulate.py                # Standalone Azure Serverless Simulator
└── test_workflow.py           # E2E Integration Test Script
```

---

## 🚀 Phase 1: Local Backend & Azure Simulation

Since cloud emulators like Azurite or Docker-based Cosmos DB Emulators can be heavy to configure, a complete **zero-dependency interactive simulation environment** has been built directly inside the repository.

### 1. Start the Local Azure Simulator
Run the simulator in a separate terminal:
```bash
python3 simulate.py
```
*What this does:*
* Starts a mock Azure Function HTTP host on `http://localhost:8080`.
* Configures local directory `local_blob_storage/` as a private Azure Blob Container.
* Instantiates an in-memory Cosmos DB SQL collection with a background thread running continuous TTL scans.
* Launches a real-time monitor displaying active ephemeral files and their countdown timers.

### 2. Run the End-to-End Workflow Integration Test
In another terminal, execute the test workflow:
```bash
python3 test_workflow.py
```
*What this does:*
1. Resolves a write-only SAS upload token and UUID path from `GetUploadUrl`.
2. Conducts a direct binary stream upload to the local filesystem container via `PUT`.
3. Registers document metadata in the Cosmos DB simulator with a **5-second TTL**.
4. Polls the file system and watches the file disappear in real-time as the simulated Cosmos DB Change Feed trigger detects the TTL expiration and executes the physical file scrub.

---

## 🛡️ Phase 2: Codifying Azure Infrastructure (Terraform)

All configurations are defined in the `terraform/` folder using the `azurerm` provider:
* **Cosmos DB SQL Container**: `azurerm_cosmosdb_sql_container` has its `default_ttl` set to `-1`. This enables TTL on the container without enforcing a global deletion rate, letting individual records declare custom retention parameters.
* **Storage Account (Blob)**: The container forces HTTPS, enforces `TLS 1.2` minimum, enables dual infrastructure encryption at rest, and disables public network access (`public_network_access_enabled = false`).
* **Managed Identities & Least-Privilege RBAC**: Connection strings are entirely omitted in favor of identity federation. A **User-Assigned Managed Identity** is mapped to the Azure Function App. Role assignments are granted via:
  1. `Storage Blob Data Contributor` via `azurerm_role_assignment`.
  2. `Cosmos DB Built-in Data Contributor` (ID `00000000-0000-0000-0000-000000000002`) via `azurerm_cosmosdb_sql_role_assignment`.

---

## 📡 Phase 3: Cosmos Change Feed Cleanup

In `functions/function_app.py`, the `@app.cosmos_db_trigger` is configured with `use_all_versions_and_deletes_mode=True`. 
This leverages Cosmos DB's **All Versions and Deletes** change feed mode to capture hard deletions caused by TTL. Because the database delete event only publishes the item ID and partition key, the container's partition key is mapped to `/filePath`. This ensures the delete event contains the storage path directly, enabling the function to execute clean-ups without doing secondary database lookups.

---

## ⚡ Phase 4: Enterprise CI/CD Pipeline (GitHub Actions)

Defined in `.github/workflows/deploy.yml`:
1. **OIDC Federation**: Avoids storing sensitive Service Principal passwords in GitHub. It authenticates directly against Microsoft Entra ID using cryptographic OpenID Connect trust.
2. **Security Screening**: Integrates **Checkov** static analysis to automatically scan Terraform plans and code for network leakage or encryption lapses, halting builds if policy violations are discovered.
3. **Automated Deployments**: Applies Terraform blueprints and builds/deploys the Python v2 programming model Function App dependencies upon merges to `main`.
