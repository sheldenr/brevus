import os
import sys
import json
import uuid
import time
import shutil
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# Configuration
PORT = 8080
BLOB_DIR = os.path.join(os.path.dirname(__file__), "local_blob_storage")
METADATA_STORE = {} # In-memory Cosmos DB simulation: { id: { ...metadata, "expires_at": timestamp } }
LOCK = threading.Lock()

# ANSI Color Codes for beautiful terminal aesthetics
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

def log(tag, message, color=Colors.CYAN):
    timestamp = time.strftime("%H:%M:%S")
    print(f"[{timestamp}] {color}{Colors.BOLD}[{tag}]{Colors.ENDC} {message}")

class AzureSimulationHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress default server log to keep CLI output clean
        pass

    def do_GET(self):
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        query = parse_qs(parsed_url.query)
        
        # Simulates: Azure Function GET /api/GetUploadUrl
        if path == "/api/GetUploadUrl":
            file_name = query.get('fileName', [None])[0]
            content_type = query.get('contentType', [None])[0]
            
            if not file_name or not content_type:
                self.send_error_response(400, "Missing 'fileName' or 'contentType' parameters.")
                return
                
            file_id = str(uuid.uuid4())
            file_path = f"uploads/{file_id}/{file_name}"
            # Local simulation upload URL
            upload_url = f"http://localhost:{PORT}/upload?filePath={file_path}"
            
            log("SAS Generator", f"Generated write-only SAS for {Colors.BOLD}{file_name}{Colors.ENDC} (expires in 5m)", Colors.BLUE)
            log("SAS Generator", f"Blob storage path: {file_path}", Colors.BLUE)
            
            response_data = {
                "id": file_id,
                "filePath": file_path,
                "uploadUrl": upload_url
            }
            self.send_json_response(200, response_data)
            
        else:
            self.send_error_response(404, "Endpoint not found.")

    def do_PUT(self):
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        query = parse_qs(parsed_url.query)
        
        # Simulates: Direct upload to Azure Blob Storage container via HTTPS PUT
        if path == "/upload":
            file_path = query.get('filePath', [None])[0]
            if not file_path:
                self.send_error_response(400, "Missing 'filePath' parameter for blob upload.")
                return
                
            # Read content length
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length <= 0:
                self.send_error_response(400, "Empty payload. Content-Length required.")
                return
                
            file_data = self.rfile.read(content_length)
            
            # Save file to simulated Blob Storage directory
            target_file = os.path.join(BLOB_DIR, file_path)
            os.makedirs(os.path.dirname(target_file), exist_ok=True)
            
            with open(target_file, "wb") as f:
                f.write(file_data)
                
            log("Blob Storage", f"File uploaded directly to: {Colors.BOLD}{file_path}{Colors.ENDC} ({content_length} bytes)", Colors.GREEN)
            self.send_json_response(201, {"status": "Uploaded successfully", "filePath": file_path})
            
        else:
            self.send_error_response(404, "Endpoint not found.")

    def do_POST(self):
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        
        # Simulates: Azure Function POST /api/CreateFileMetadata
        if path == "/api/CreateFileMetadata":
            content_length = int(self.headers.get('Content-Length', 0))
            try:
                post_data = json.loads(self.rfile.read(content_length))
            except ValueError:
                self.send_error_response(400, "Invalid JSON body.")
                return
                
            doc_id = post_data.get('id')
            file_path = post_data.get('filePath')
            file_name = post_data.get('fileName')
            content_type = post_data.get('contentType')
            ttl = post_data.get('ttl', 86400) # ttl in seconds (default 24h)
            
            if not all([doc_id, file_path, file_name, content_type]):
                self.send_error_response(400, "Missing required fields: 'id', 'filePath', 'fileName', 'contentType'")
                return
                
            expires_at = time.time() + float(ttl)
            
            with LOCK:
                METADATA_STORE[doc_id] = {
                    "id": doc_id,
                    "filePath": file_path,
                    "fileName": file_name,
                    "contentType": content_type,
                    "ttl": int(ttl),
                    "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "expires_at": expires_at
                }
                
            log("Cosmos DB", f"Stored document for {Colors.BOLD}{file_name}{Colors.ENDC} with TTL of {ttl}s", Colors.HEADER)
            log("Cosmos DB", f"Expiration set to dynamic TTL sweep at {time.strftime('%H:%M:%S', time.localtime(expires_at))}", Colors.HEADER)
            
            self.send_json_response(201, {
                "status": "Metadata logged successfully",
                "id": doc_id,
                "ttl": ttl
            })
            
        else:
            self.send_error_response(404, "Endpoint not found.")

    def send_json_response(self, status, data):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode('utf-8'))

    def send_error_response(self, status, message):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({"error": message}).encode('utf-8'))

def cosmos_ttl_scanner():
    """
    Background worker simulating the Cosmos DB Lifecycle Manager sweep.
    Checks items every second and triggers Change Feed deletions when TTL expires.
    """
    while True:
        time.sleep(1.0)
        now = time.time()
        expired_docs = []
        
        with LOCK:
            for doc_id, doc in list(METADATA_STORE.items()):
                if now >= doc["expires_at"]:
                    expired_docs.append(doc)
                    del METADATA_STORE[doc_id]
                    
        for doc in expired_docs:
            trigger_cosmos_change_feed_cleanup(doc)

def trigger_cosmos_change_feed_cleanup(doc):
    """
    Simulates: Azure Cosmos DB Change Feed "All versions and deletes" Mode triggering 
    a cleanup function when a document's TTL expires.
    """
    log("Cosmos Lifecycle", f"Document ID '{doc['id']}' expired. TTL elapsed.", Colors.WARNING)
    
    # 1. Prepare simulated Change Feed Event in "All Versions and Deletes" Mode
    change_feed_event = {
        "id": doc["id"],
        "partitionKey": {
            "filePath": doc["filePath"]
        },
        "operationType": "Delete",
        "timeToLiveExpired": True,
        "_metadata": {
            "operationType": "Delete",
            "timeToLiveExpired": True
        }
    }
    
    log("Change Feed", "CosmosDBTrigger received deletion event. Triggering cleanup function...", Colors.WARNING)
    
    # 2. Invoke simulated function app logic (CosmosTriggerCleanup)
    filePath = change_feed_event["partitionKey"]["filePath"]
    doc_id = change_feed_event["id"]
    
    # Check if file exists in simulated Blob Storage
    local_file_path = os.path.join(BLOB_DIR, filePath)
    
    if os.path.exists(local_file_path):
        log("Function Cleanup", f"Scrubbing physical file: '{filePath}'", Colors.FAIL)
        try:
            os.remove(local_file_path)
            # Try to clean up empty directories if possible
            parent_dir = os.path.dirname(local_file_path)
            if not os.listdir(parent_dir):
                os.rmdir(parent_dir)
                grandparent_dir = os.path.dirname(parent_dir)
                if not os.listdir(grandparent_dir) and grandparent_dir != BLOB_DIR:
                    os.rmdir(grandparent_dir)
            log("Function Cleanup", f"File scrubbed successfully from Blob Container.", Colors.GREEN)
            
            # Send Notification Alert
            simulate_notification_alert(filePath, doc_id)
        except Exception as e:
            log("Function Cleanup", f"Failed to scrub file: {str(e)}", Colors.FAIL)
    else:
        log("Function Cleanup", f"File '{filePath}' already removed or does not exist.", Colors.WARNING)

def simulate_notification_alert(file_path, doc_id):
    """
    Simulates notification delivery via Azure Communication Services / Event Grid Topic.
    """
    log("Event Alert", "Dispatching secure deletion verification...", Colors.BLUE)
    border = "=" * 80
    print(f"\n{Colors.BLUE}{Colors.BOLD}{border}")
    print(f" AZURE EVENT DRIFT ALERT: SYSTEM-AUTOMATED SCRUB CONFIRMED")
    print(f" {border}")
    print(f" Source:          Cosmos DB TTL Change Feed Event")
    print(f" Status:          SUCCESS")
    print(f" Deleted Record:  {doc_id}")
    print(f" File Scrubbed:   {file_path}")
    print(f" Timestamp:       {time.strftime('%Y-%m-%dT%H:%M:%S')} (UTC)")
    print(f" Notification:    Email sent via Azure Communication Services (Email SDK)")
    print(f"{border}{Colors.ENDC}\n")

def console_monitor():
    """
    Background worker that updates the screen with active items in the simulated Cosmos DB
    and their remaining TTL.
    """
    while True:
        time.sleep(2.0)
        with LOCK:
            if not METADATA_STORE:
                continue
                
            print(f"\n{Colors.CYAN}--- ACTIVE EPHEMERAL FILES MONITOR ---{Colors.ENDC}")
            print(f"{'Document ID':<40} | {'File Path':<35} | {'Remaining TTL':<15}")
            print("-" * 96)
            now = time.time()
            for doc_id, doc in METADATA_STORE.items():
                rem = max(0.0, doc["expires_at"] - now)
                print(f"{doc_id:<40} | {doc['filePath']:<35} | {rem:.1f}s")
            print(f"{Colors.CYAN}--------------------------------------{Colors.ENDC}\n")

def run_server():
    # Clean old simulated storage if exists
    if os.path.exists(BLOB_DIR):
        shutil.rmtree(BLOB_DIR)
    os.makedirs(BLOB_DIR, exist_ok=True)
    
    server_address = ('', PORT)
    httpd = HTTPServer(server_address, AzureSimulationHandler)
    
    print(f"\n{Colors.GREEN}{Colors.BOLD}==========================================================================")
    print(f" AZURE SERVERLESS DATA GOVERNANCE SIMULATOR")
    print(f"=========================================================================={Colors.ENDC}")
    print(f"  * Local HTTP API:    http://localhost:{PORT}")
    print(f"  * Blob storage path: {BLOB_DIR}")
    print(f"  * Cosmos DB:         Simulated in-memory collection (TTL scans active)")
    print(f"  * Change Feed:       Triggering on deletions / TTL expirations")
    print(f"  * Alert System:      Integrated console alerts + simulated ACS Email Client")
    print(f"==========================================================================\n")
    
    # Start Cosmos DB TTL background scanner
    scanner_thread = threading.Thread(target=cosmos_ttl_scanner, daemon=True)
    scanner_thread.start()
    
    # Start console monitor for TTL countdowns
    monitor_thread = threading.Thread(target=console_monitor, daemon=True)
    monitor_thread.start()
    
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print(f"\n{Colors.WARNING}Shutting down simulator...{Colors.ENDC}")
        httpd.server_close()

if __name__ == "__main__":
    run_server()
