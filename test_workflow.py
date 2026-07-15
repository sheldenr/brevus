import os
import time
import json
import urllib.request
import urllib.error

# Configuration
API_URL = "http://localhost:8080"
TEST_FILE_NAME = "ephemeral_test.txt"
TEST_FILE_CONTENT = b"This is a secure ephemeral document governed by Azure Cosmos DB TTL policy."
TTL_SECONDS = 5

class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'

def log(step, msg, color=Colors.CYAN):
    print(f"{color}{Colors.BOLD}[Step {step}]{Colors.ENDC} {msg}")

def run_test():
    print(f"\n{Colors.HEADER}=== RUNNING END-TO-END DATA GOVERNANCE FLOW TEST ==={Colors.ENDC}\n")
    
    # ----------------------------------------------------
    # Step 1: Request Upload URL (GetUploadUrl)
    # ----------------------------------------------------
    log(1, f"Requesting Upload URL from API for {TEST_FILE_NAME}...", Colors.BLUE)
    url = f"{API_URL}/api/GetUploadUrl?fileName={TEST_FILE_NAME}&contentType=text/plain"
    
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req) as response:
            res_body = json.loads(response.read().decode())
    except urllib.error.URLError as e:
        print(f"\n{Colors.FAIL}Error: Could not connect to the local simulator server at {API_URL}.{Colors.ENDC}")
        print(f"{Colors.WARNING}Please start the simulator in a separate terminal using: python3 simulate.py{Colors.ENDC}\n")
        return
        
    doc_id = res_body["id"]
    file_path = res_body["filePath"]
    upload_url = res_body["uploadUrl"]
    
    log(1, f"Received Document ID: {Colors.BOLD}{doc_id}{Colors.ENDC}", Colors.BLUE)
    log(1, f"Received Blob Path:   {Colors.BOLD}{file_path}{Colors.ENDC}", Colors.BLUE)
    
    # ----------------------------------------------------
    # Step 2: Upload File Directly via HTTPS PUT
    # ----------------------------------------------------
    log(2, f"Uploading file directly to storage container via HTTPS PUT...", Colors.GREEN)
    
    req_put = urllib.request.Request(
        upload_url,
        data=TEST_FILE_CONTENT,
        method="PUT",
        headers={"Content-Length": len(TEST_FILE_CONTENT)}
    )
    
    with urllib.request.urlopen(req_put) as response:
        upload_res = json.loads(response.read().decode())
        
    log(2, "Upload successful!", Colors.GREEN)
    
    # Verify local file presence
    local_blob_path = os.path.join(os.path.dirname(__file__), "local_blob_storage", file_path)
    file_exists_before = os.path.exists(local_blob_path)
    log(2, f"Verifying physical storage presence: "
           f"{Colors.GREEN if file_exists_before else Colors.FAIL}{'PRESENT' if file_exists_before else 'ABSENT'}{Colors.ENDC}", Colors.GREEN)
    
    # ----------------------------------------------------
    # Step 3: Create Metadata in Cosmos DB (CreateFileMetadata)
    # ----------------------------------------------------
    log(3, f"Logging document metadata to Cosmos DB (TTL set to {TTL_SECONDS} seconds)...", Colors.HEADER)
    
    metadata_payload = {
        "id": doc_id,
        "filePath": file_path,
        "fileName": TEST_FILE_NAME,
        "contentType": "text/plain",
        "ttl": TTL_SECONDS
    }
    
    req_post = urllib.request.Request(
        f"{API_URL}/api/CreateFileMetadata",
        data=json.dumps(metadata_payload).encode(),
        method="POST",
        headers={"Content-Type": "application/json"}
    )
    
    with urllib.request.urlopen(req_post) as response:
        meta_res = json.loads(response.read().decode())
        
    log(3, f"Cosmos DB write confirmed! Metadata ID={meta_res['id']}", Colors.HEADER)
    
    # ----------------------------------------------------
    # Step 4: Monitor Expired Deletion
    # ----------------------------------------------------
    log(4, f"Waiting for Cosmos DB dynamic TTL sweep ({TTL_SECONDS} seconds)...", Colors.WARNING)
    
    # Poll for file deletion
    start_time = time.time()
    deleted = False
    
    for i in range(12):
        time.sleep(1.0)
        elapsed = time.time() - start_time
        file_present = os.path.exists(local_blob_path)
        
        if not file_present:
            print(f"\n{Colors.GREEN}{Colors.BOLD}>>> SUCCESS! The physical file has disappeared after {elapsed:.1f}s!{Colors.ENDC}")
            print(f"{Colors.GREEN}Cosmos DB TTL cleanup pipeline worked flawlessly.{Colors.ENDC}\n")
            deleted = True
            break
        else:
            print(f"  [{elapsed:.1f}s] Checking file presence... STILL PRESENT")
            
    if not deleted:
        print(f"\n{Colors.FAIL}Error: File was not deleted within the expected window.{Colors.ENDC}\n")

if __name__ == "__main__":
    run_test()
