# cloudflare_storage.py
import os
import json
import boto3
from botocore.exceptions import NoCredentialsError

class CloudflareStorage:
    def __init__(self, access_key, secret_key, endpoint_url, region_name='auto'):
        """
        Initializes the connection to Cloudflare's S3-compatible storage.

        :param access_key: Cloudflare R2 access key.
        :param secret_key: Cloudflare R2 secret key.
        :param endpoint_url: URL endpoint for Cloudflare's S3-compatible storage.
        :param region_name: Region name, default is 'auto' for Cloudflare.
        """
        self.session = boto3.session.Session()
        self.client = self.session.client('s3',
                                          region_name=region_name,
                                          endpoint_url=endpoint_url,
                                          aws_access_key_id=access_key,
                                          aws_secret_access_key=secret_key)

    def upload_file(self, file_name, bucket_name, object_name=None):
        """
        Upload a file to an R2 bucket.

        :param file_name: File to upload.
        :param bucket_name: Bucket to upload to.
        :param object_name: S3 object name. If not specified, file_name is used.
        :return: True if file was uploaded, else False.
        """
        if object_name is None:
            object_name = file_name

        try:
            response = self.client.upload_file(file_name, bucket_name, object_name)
            print(f"File {file_name} uploaded to {bucket_name}/{object_name}")
            return True
        except NoCredentialsError:
            print("Credentials not available")
            return False
        except Exception as e:
            print(f"Failed to upload {file_name}: {e}")
            return False

    def folder_exists(self, bucket_name, folder_name):
        """
        Check if a folder exists in a specified bucket.

        :param bucket_name: Name of the bucket.
        :param folder_name: The folder name (prefix) to check for.
        :return: True if the folder exists, False otherwise.
        """
        # Ensure the folder name ends with a '/' to accurately match the folder structure
        if not folder_name.endswith('/'):
            folder_name += '/'

        response = self.client.list_objects_v2(
            Bucket=bucket_name,
            Prefix=folder_name,
            MaxKeys=1  # We only need to find one object to confirm the folder exists
        )
        return 'Contents' in response and len(response['Contents']) > 0

    def upload_folder(self, local_folder_path, bucket_name, cloudflare_folder_prefix=''):
        """
        Recursively uploads a folder and its contents to Cloudflare R2, maintaining the folder structure,
        and creates an index file in the top-level directory listing all uploaded files.

        :param local_folder_path: The local path to the folder to upload.
        :param bucket_name: The Cloudflare R2 bucket to upload to.
        :param cloudflare_folder_prefix: The prefix (including any folder structure) under which to store the files in R2.
        """
        uploaded_files = []  # To keep track of all uploaded files for the index

        for root, dirs, files in os.walk(local_folder_path):
            for filename in files:
                local_path = os.path.join(root, filename)
                relative_path = os.path.relpath(local_path, start=local_folder_path)
                cloudflare_object_name = os.path.join(cloudflare_folder_prefix, relative_path)
                cloudflare_object_name = cloudflare_object_name.replace('\\', '/')

                # Upload the file
                if self.upload_file(local_path, bucket_name, cloudflare_object_name):
                    uploaded_files.append(cloudflare_object_name)  # Add successful uploads to the list

        # After all files are uploaded, create and upload the index.json file
        index_file_path = '/tmp/index.json' if cloudflare_folder_prefix else cloudflare_folder_prefix + 'index.json'
        with open(index_file_path, 'w') as index_file:
            json.dump(uploaded_files, index_file)

        # Upload the index file
        cloudflare_object_name = os.path.join(cloudflare_folder_prefix, 'index.json')
        cloudflare_object_name = cloudflare_object_name.replace('\\', '/')
        self.upload_file(index_file_path, bucket_name, cloudflare_object_name)

        print(f"Uploaded index file to Cloudflare R2 storage at {cloudflare_folder_prefix}index.json")

    def delete_folder_contents(self, bucket_name, folder_prefix):
        """
        Deletes all contents of a specified folder within the Cloudflare R2 bucket.

        :param bucket_name: The name of the Cloudflare R2 bucket.
        :param folder_prefix: The prefix of the folder to delete contents from. Must end with '/'.
        """
        # Ensure the folder prefix ends with '/' to avoid accidentally deleting unintended objects
        if not folder_prefix.endswith('/'):
            folder_prefix += '/'

        # List all objects in the folder
        response = self.client.list_objects_v2(Bucket=bucket_name, Prefix=folder_prefix)
        objects = response.get('Contents', [])

        # If there are objects to delete, prepare and execute the deletion
        if objects:
            delete_keys = {'Objects': [{'Key': obj['Key']} for obj in objects]}
            delete_response = self.client.delete_objects(Bucket=bucket_name, Delete=delete_keys)
            print(f"Deleted objects: {delete_response}")
        else:
            print("No objects found to delete.")
