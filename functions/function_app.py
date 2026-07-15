import os
import logging
import uuid
import datetime
from datetime import datetime, timezone, timedelta
import azure.functions as func
from azure.identity import DefaultAzureCredential
from azure.cosmos import CosmosClient
from azure.storage.blob import BlobServiceClient, generate_blob_sas, BlobSasPermissions
from azure.communication.email import EmailClient

# Create the Function App
app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

# =====================================================================
# HELPERS
# =====================================================================

def parse_connection_string(conn_str):
    """
    Parses a storage connection string to extract account name and key.
    Handles local development storage (Azurite) automatically.
    """
    if conn_str == "UseDevelopmentStorage=true":
        return (
            "devstoreaccount1",
            "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw=="
        )
    
    parts = dict(item.split('=', 1) for item in conn_str.split(';') if '=' in item)
    return parts.get('AccountName'), parts.get('AccountKey')

def get_cosmos_client():
    """
    Establishes a CosmosClient connection. Falls back to DefaultAzureCredential
    if ConnectionString is not set (supporting Managed Identities).
    """
    conn_str = os.environ.get("CosmosDBConnectionString")
    if conn_str:
        # Disable SSL verification only if running against local Cosmos DB Emulator
        if "localhost" in conn_str or "127.0.0.1" in conn_str:
            return CosmosClient.from_connection_string(conn_str, connection_verify_certificate=False)
        return CosmosClient.from_connection_string(conn_str)
    
    endpoint = os.environ.get("CosmosDBEndpoint")
    if endpoint:
        credential = DefaultAzureCredential()
        if "localhost" in endpoint or "127.0.0.1" in endpoint:
            return CosmosClient(endpoint, credential=credential, connection_verify_certificate=False)
        return CosmosClient(endpoint, credential=credential)
        
    # Fallback to standard local emulator connection string
    default_emulator_conn = "AccountEndpoint=https://localhost:8081/;AccountKey=C2y6yDjf5/R+ob0N8A7Cgv30VRDJIWEHLM+4QDU5DE2nQ9nDuVTqobD4b8mGGyPMbIZnqyMsEcaGQy67XIw/Jw=="
    return CosmosClient.from_connection_string(default_emulator_conn, connection_verify_certificate=False)

def delete_blob_from_storage(file_path: str):
    """
    Deletes the target blob from Blob Storage container.
    """
    conn_str = os.environ.get("BlobStorageConnectionString")
    container_name = os.environ.get("BlobContainerName", "ephemeral-files")
    
    if conn_str:
        blob_service_client = BlobServiceClient.from_connection_string(conn_str)
    else:
        account_url = os.environ.get("BlobStorageAccountUrl")
        if not account_url:
            raise ValueError("Either BlobStorageConnectionString or BlobStorageAccountUrl must be set.")
        credential = DefaultAzureCredential()
        blob_service_client = BlobServiceClient(account_url, credential=credential)
        
    container_client = blob_service_client.get_container_client(container_name)
    blob_client = container_client.get_blob_client(file_path)
    blob_client.delete_blob()

def extract_file_path(doc: dict) -> str:
    """
    Extracts the filePath property from a change feed document in All Versions & Deletes mode.
    Handles various shapes of delete/TTL structures.
    """
    # 1. Check in root
    if 'filePath' in doc:
        return doc['filePath']
    
    # 2. Check in 'current' representation (if insert/replace)
    current = doc.get('current', {})
    if 'filePath' in current:
        return current['filePath']
        
    # 3. Check in partitionKey metadata
    pk = doc.get('partitionKey')
    if pk:
        if isinstance(pk, list) and len(pk) > 0:
            return pk[0]
        elif isinstance(pk, dict):
            # Return first value in dictionary
            return next(iter(pk.values()))
        elif isinstance(pk, str):
            return pk
            
    # 4. Fallback: Check if partitionKey value is stored under a metadata object
    metadata = doc.get('metadata', doc.get('_metadata', {}))
    if 'partitionKey' in metadata:
        pk_meta = metadata.get('partitionKey')
        if isinstance(pk_meta, list) and len(pk_meta) > 0:
            return pk_meta[0]
        elif isinstance(pk_meta, dict):
            return next(iter(pk_meta.values()))
            
    # 5. Fallback to doc ID
    return doc.get('id')

def send_cleanup_notification(file_path: str, doc_id: str):
    """
    Sends an email notification via Azure Communication Services.
    """
    conn_str = os.environ.get("AzureCommunicationServicesEmailConnectionString")
    sender_address = os.environ.get("EmailSenderAddress", "donotreply@yourdomain.com")
    recipient_address = os.environ.get("EmailRecipientAddress", "admin@yourdomain.com")
    
    # If not configured or dummy config, run in local simulation log mode
    if not conn_str or "dummy" in conn_str:
        logging.info(f"[SIMULATED NOTIFICATION] Secure file scrub executed. "
                     f"File path: '{file_path}' (Metadata ID: {doc_id}) has been deleted from Blob storage.")
        return
        
    try:
        email_client = EmailClient.from_connection_string(conn_str)
        message = {
            "senderAddress": sender_address,
            "content": {
                "subject": "Data Scrub Notification: Ephemeral File Deleted",
                "plainText": f"Data Governance Policy execution confirmed.\n\n"
                             f"File: {file_path}\n"
                             f"Metadata ID: {doc_id}\n"
                             f"Timestamp: {datetime.now(timezone.utc).isoformat()}\n\n"
                             f"This file has been programmatically scrubbed from Azure Blob Storage following Cosmos DB TTL document expiration."
            },
            "recipients": {
                "to": [{"address": recipient_address}]
            }
        }
        poller = email_client.begin_send(message)
        result = poller.result()
        logging.info(f"Notification email sent successfully. Message ID: {result.get('messageId')}")
    except Exception as e:
        logging.error(f"Failed to send email notification: {str(e)}")

# =====================================================================
# HTTP TRIGGER 1: GetUploadUrl
# =====================================================================

@app.route(route="GetUploadUrl", methods=["GET"])
def GetUploadUrl(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Processing GetUploadUrl request.")
    
    file_name = req.params.get('fileName')
    content_type = req.params.get('contentType')
    
    if not file_name or not content_type:
        return func.HttpResponse(
            "Please pass 'fileName' and 'contentType' as query parameters.",
            status_code=400
        )
        
    conn_str = os.environ.get("BlobStorageConnectionString")
    container_name = os.environ.get("BlobContainerName", "ephemeral-files")
    
    if not conn_str:
        return func.HttpResponse(
            "BlobStorageConnectionString environment variable is not configured.",
            status_code=500
        )
        
    try:
        account_name, account_key = parse_connection_string(conn_str)
        
        # Generate unique folder/id for this upload to prevent collisions
        file_id = str(uuid.uuid4())
        file_path = f"uploads/{file_id}/{file_name}"
        
        # Define 5 minute expiry for SAS Token
        expiry_time = datetime.now(timezone.utc) + timedelta(minutes=5)
        
        # Generate write-only SAS signature
        sas_token = generate_blob_sas(
            account_name=account_name,
            container_name=container_name,
            blob_name=file_path,
            account_key=account_key,
            permission=BlobSasPermissions(write=True),
            expiry=expiry_time
        )
        
        # Construct target URL
        if conn_str == "UseDevelopmentStorage=true":
            upload_url = f"http://127.0.0.1:10000/devstoreaccount1/{container_name}/{file_path}?{sas_token}"
        else:
            upload_url = f"https://{account_name}.blob.core.windows.net/{container_name}/{file_path}?{sas_token}"
            
        return func.HttpResponse(
            body=f'{{"id": "{file_id}", "filePath": "{file_path}", "uploadUrl": "{upload_url}"}}',
            status_code=200,
            mimetype="application/json"
        )
        
    except Exception as e:
        logging.error(f"Error generating upload SAS: {str(e)}")
        return func.HttpResponse(
            body=f'{{"error": "Failed to generate upload URL: {str(e)}"}}',
            status_code=500,
            mimetype="application/json"
        )

# =====================================================================
# HTTP TRIGGER 2: CreateFileMetadata
# =====================================================================

@app.route(route="CreateFileMetadata", methods=["POST"])
def CreateFileMetadata(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Processing CreateFileMetadata request.")
    
    try:
        req_body = req.get_json()
    except ValueError:
        return func.HttpResponse("Invalid JSON payload.", status_code=400)
        
    doc_id = req_body.get('id')
    file_path = req_body.get('filePath')
    file_name = req_body.get('fileName')
    content_type = req_body.get('contentType')
    ttl = req_body.get('ttl', 86400) # Defaults to 24 hours (86400 seconds)
    
    if not all([doc_id, file_path, file_name, content_type]):
        return func.HttpResponse(
            "Missing required fields: 'id', 'filePath', 'fileName', 'contentType'.",
            status_code=400
        )
        
    try:
        client = get_cosmos_client()
        database_name = os.environ.get("CosmosDBDatabaseName", "EphemeralDb")
        container_name = os.environ.get("CosmosDBContainerName", "FileMetadata")
        
        db = client.get_database_client(database_name)
        container = db.get_container_client(container_name)
        
        # Populate custom TTL in seconds
        document = {
            "id": doc_id,
            "filePath": file_path,
            "fileName": file_name,
            "contentType": content_type,
            "ttl": int(ttl),
            "createdAt": datetime.now(timezone.utc).isoformat()
        }
        
        # Save to Cosmos DB
        container.create_item(body=document)
        
        return func.HttpResponse(
            body=f'{{"status": "Metadata logged successfully", "id": "{doc_id}", "ttl": {ttl}}}',
            status_code=201,
            mimetype="application/json"
        )
        
    except Exception as e:
        logging.error(f"Error saving file metadata to Cosmos DB: {str(e)}")
        return func.HttpResponse(
            body=f'{{"error": "Failed to store metadata: {str(e)}"}}',
            status_code=500,
            mimetype="application/json"
        )

# =====================================================================
# COSMOS DB TRIGGER: CosmosTriggerCleanup
# =====================================================================

@app.cosmos_db_trigger(
    arg_name="documents",
    container_name="FileMetadata",
    database_name="EphemeralDb",
    connection="CosmosDBConnectionString",
    lease_container_name="leases",
    create_lease_container_if_not_exists=True,
    use_all_versions_and_deletes_mode=True # REQUIRED to capture TTL deletions
)
def CosmosTriggerCleanup(documents: func.DocumentList):
    logging.info(f"Cosmos DB Change Feed Trigger fired. Received {len(documents)} document events.")
    
    for doc in documents:
        # Extract metadata
        metadata = doc.get('metadata', doc.get('_metadata', {}))
        operation_type = metadata.get('operationType', doc.get('operationType', ''))
        ttl_expired = metadata.get('timeToLiveExpired', doc.get('timeToLiveExpired', False))
        
        doc_id = doc.get('id')
        
        logging.info(f"Change Feed Event: ID={doc_id}, Operation={operation_type}, TtlExpired={ttl_expired}")
        
        # Trigger cleanup if the operation is a Delete OR timeToLiveExpired is True
        if operation_type == "Delete" or ttl_expired:
            file_path = extract_file_path(doc)
            
            if not file_path:
                logging.warning(f"Could not resolve file path for document event ID={doc_id}.")
                continue
                
            logging.info(f"Targeting physical file scrub for path: '{file_path}' due to TTL expiration.")
            
            try:
                # physical delete from blob
                delete_blob_from_storage(file_path)
                logging.info(f"Successfully scrubbed blob path: '{file_path}'")
                
                # Send confirmation notification
                send_cleanup_notification(file_path, doc_id)
                
            except Exception as e:
                logging.error(f"Error scrubbing file '{file_path}': {str(e)}")
