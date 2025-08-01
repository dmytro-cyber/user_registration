import mimetypes
from typing import IO, Union

import boto3
from botocore.exceptions import BotoCoreError, ClientError, ConnectionError, HTTPClientError, NoCredentialsError

from exceptions.storage import S3ConnectionError, S3FileUploadError
from storages import S3StorageInterface


class S3StorageClient(S3StorageInterface):
    def __init__(self, endpoint_url: str, access_key: str, secret_key: str, bucket_name: str):
        """
        Initialize S3 Storage Client.

        :param endpoint_url: S3-compatible storage endpoint.
        :param access_key: Access key for authentication.
        :param secret_key: Secret key for authentication.
        :param bucket_name: Name of the bucket where files will be stored.
        """
        self._endpoint_url = endpoint_url
        self._access_key = access_key
        self._secret_key = secret_key
        self._bucket_name = bucket_name

        self._s3_client = boto3.client(
            "s3",
            endpoint_url=self._endpoint_url,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
        )

    async def upload_fileobj(self, file_key: str, file_obj: IO) -> None:
        try:
            content_type, _ = mimetypes.guess_type(file_key)
            extra_args = {"ContentType": content_type} if content_type else {}

            self._s3_client.upload_fileobj(
                Fileobj=file_obj, Bucket=self._bucket_name, Key=file_key, ExtraArgs=extra_args
            )
        except ClientError as e:
            raise e

    def upload_file(self, file_name: str, file_data: Union[bytes, bytearray]) -> None:
        """
        Uploads a file to the S3-compatible storage.

        :param file_name: The name of the file to be stored.
        :param file_data: The file data in bytes.
        :return: URL of the uploaded file.
        """
        try:
            self._s3_client.put_object(
                Bucket=self._bucket_name, Key=file_name, Body=file_data, ContentType="application/octet-stream"
            )
        except (ConnectionError, HTTPClientError, NoCredentialsError) as e:
            raise S3ConnectionError(f"Failed to connect to S3 storage: {str(e)}") from e
        except BotoCoreError as e:
            raise S3FileUploadError(f"Failed to upload to S3 storage: {str(e)}") from e

    def get_file_url(self, file_name: str) -> str:
        """
        Generate a public URL for a file stored in the S3-compatible storage.

        :param file_name: The name of the file stored in the bucket.
        :return: The full URL to access the file.
        """
        return f"{self._endpoint_url}/{self._bucket_name}/{file_name}"

    def delete_file(self, file_name: str) -> None:
        """
        Deletes a file from the S3-compatible storage.

        :param file_name: The name of the file to be deleted.
        """
        try:
            self._s3_client.delete_object(Bucket=self._bucket_name, Key=file_name)
        except ClientError as e:
            raise S3ConnectionError(f"Failed to delete file from S3 storage: {str(e)}") from e
