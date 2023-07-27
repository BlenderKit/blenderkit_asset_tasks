from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials
import os
import json

# Define the scope for the Google Drive API.
# This scope allows for full read/write access to the authenticated user's account.
SCOPES = ['https://www.googleapis.com/auth/drive']


# Initialize the Google Drive service.
# This function handles authentication and returns a service object that can be used to interact with the API.
def init_drive():
    creds = None
    # Use service account credentials to authenticate.
    creds = Credentials.from_service_account_info(
        json.loads(os.getenv('GDRIVE_SERVICE_ACCOUNT_KEY')), scopes=SCOPES)

    # Build the service object.
    service = build('drive', 'v3', credentials=creds)

    return service


# List all files in a specific folder in Google Drive.
def list_files_in_folder(service, folder_id):
    # Query the API to list the files in the folder.
    results = service.files().list(
        pageSize=10, q=f"'{folder_id}' in parents",
        includeItemsFromAllDrives=True,
        supportsAllDrives=True, fields="nextPageToken, files(id, name)").execute()
    items = results.get('files', [])

    # Print each file's name and ID.
    for item in items:
        print(f"Found file: {item['name']} ({item['id']})")


# Check if a specific file exists in a Google Drive folder.
def file_exists(service, filename, folder_id):
    # Query the API to search for the file in the folder.
    query = f"name='{filename}' and '{folder_id}' in parents and trashed=false"
    results = service.files().list(q=query,
                                   includeItemsFromAllDrives=True,
                                   supportsAllDrives=True,
                                   fields='files(id, name)'
                                   ).execute()
    items = results.get('files', [])
    # If the file was found, return True. Otherwise, return False.
    return len(items) > 0


# Ensure that a specific folder exists in Google Drive.
# If the folder doesn't exist, it's created.
def ensure_folder_exists(service, folder_name, parent_id='', drive_id='root'):
    # Query the API to search for the folder.
    query = f"name='{folder_name}' and '{parent_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
    response = service.files().list(q=query,
                                    corpora='drive',
                                    driveId=drive_id,
                                    includeItemsFromAllDrives=True,
                                    supportsAllDrives=True,
                                    fields='files(id, name)').execute()
    items = response.get('files', [])

    # If the folder exists, return its ID. Otherwise, create it and return its new ID.
    if items:
        return items[0]['id']
    else:
        metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [parent_id]
        }
        folder = service.files().create(body=metadata,
                                        supportsAllDrives=True,
                                        # driveId=drive_id,
                                        fields='id').execute()
        return folder['id']


# Upload a file to a specific folder in Google Drive.
def upload_file_to_folder(service, file_path, folder_id):
    # Prepare the metadata for the new file.
    file_metadata = {
        'name': os.path.basename(file_path),
        'parents': [folder_id]
    }
    media = MediaFileUpload(file_path)

    # Upload the file and return its new ID.
    file = service.files().create(body=file_metadata,
                                  media_body=media,
                                  fields='id',
                                  supportsAllDrives=True).execute()
    print(f"File ID: {file.get('id')}")
