# Brevus - Self-Deleting Files on Azure

So basically, this is a project called Brevus. It lets you upload files to Azure storage and then deletes them automatically after a timer runs out. 

We do this because uploading huge files through a normal server makes the server really slow. Instead, the client gets a special temporary link and uploads the files straight to Azure Blob Storage. Then we save a note in a database (Cosmos DB) saying "delete this file in X seconds." When the timer is up, the database deletes the note, and that triggers a function to go delete the actual file.

---

## How It Works

Here is a simple explanation of how the data flows:

1. The client asks the backend for a special upload link. The backend gives them a link that only works for 5 minutes.
2. The client uploads the file directly to Azure Blob Storage using that link.
3. The client writes a note in Cosmos DB with a TTL (Time-to-Live), which is just a countdown timer in seconds.
4. When the timer runs out, Cosmos DB deletes the note.
5. The Cosmos DB Change Feed sees that the note was deleted and tells the Azure Function.
6. The Azure Function deletes the actual file from storage and sends an email to say it's done.

---

## What is in the folders

* **functions/**: The Python code for the Azure Functions.
* **terraform/**: The setup files to build all the Azure resources (like the database, storage, and permissions) so you don't have to do it manually.
* **.github/workflows/deploy.yml**: This deploys the project to Azure automatically when you push code to GitHub. It also scans the setup files to make sure there are no security holes.
* **simulate.py**: A local simulator so you can test everything on your computer without needing a real Azure account or paying for anything.
* **test_workflow.py**: A script to test the whole workflow. It uploads a test file, waits 5 seconds, and makes sure it got deleted.

---

## Running It Locally

You can test this project on your machine. You don't need an Azure account.

### 1. Start the Simulator
Open a terminal and run:
```bash
python3 simulate.py
```
This runs a local server on port 8080. It saves files in a folder called `local_blob_storage` and keeps track of the database timers in memory.

### 2. Run the Test
Open another terminal and run:
```bash
python3 test_workflow.py
```
This script asks for an upload URL, uploads a test file, sets a 5-second timer, and checks to see if the file disappears from the local folder. You'll see the file get created and then deleted after 5 seconds.

---

## Deploying to Azure

If you want to put this on the real internet, the files in the `terraform/` folder set it all up:
* It sets up Cosmos DB with the TTL feature turned on.
* It sets up Blob Storage and turns off public access so no random people can look at your files.
* It uses Managed Identities, which is just a secure way for the services to talk to each other without having to type passwords in the code.
