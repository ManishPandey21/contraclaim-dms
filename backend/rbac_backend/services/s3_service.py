from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from ..core.config import settings


class S3Service:
    """
    S3 abstraction. Uses environment-driven settings and falls back to defaults.
    """

    def __init__(
        self,
        bucket_name: Optional[str] = None,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        region: Optional[str] = None,
        endpoint: Optional[str] = None,
        force_path_style: Optional[bool] = None,
    ) -> None:
        self.bucket_name = (
            bucket_name
            or getattr(settings, "S3_BUCKET", None)
            or getattr(settings, "AWS_BUCKET_NAME", None)
            or "local-placeholder"
        )
        self._session = boto3.session.Session(
            aws_access_key_id=access_key
            or getattr(settings, "S3_ACCESS_KEY", None)
            or getattr(settings, "AWS_ACCESS_KEY_ID", None),
            aws_secret_access_key=secret_key
            or getattr(settings, "S3_SECRET_KEY", None)
            or getattr(settings, "AWS_SECRET_ACCESS_KEY", None),
            region_name=region
            or getattr(settings, "S3_REGION", None)
            or getattr(settings, "AWS_REGION", None),
        )
        client_config = BotoConfig(s3={"addressing_style": "path"} if force_path_style or getattr(settings, "S3_FORCE_PATH_STYLE", False) else None)
        self._client = self._session.client(
            "s3",
            endpoint_url=endpoint or getattr(settings, "S3_ENDPOINT", None),
            config=client_config,
        )

    def _object_key(self, key: str) -> str:
        # Normalize leading slash
        return key[1:] if key.startswith("/") else key

    async def create_folder_placeholder(self, path: str) -> None:
        key = self._object_key(path.rstrip("/") + "/")
        try:
            self._client.put_object(Bucket=self.bucket_name, Key=key, Body=b"")
        except (BotoCoreError, ClientError):
            # Log suppressed; caller can decide error handling
            return None

    async def upload_bytes(self, key: str, data: bytes, content_type: Optional[str] = None) -> str:
        object_key = self._object_key(key)
        extra_args = {}
        if content_type:
            extra_args["ContentType"] = content_type
        sse = getattr(settings, "AWS_S3_SERVER_SIDE_ENCRYPTION", None) or getattr(settings, "S3_SERVER_SIDE_ENCRYPTION", None)
        if sse:
            extra_args["ServerSideEncryption"] = sse
        kms_key_id = getattr(settings, "AWS_S3_KMS_KEY_ID", None) or getattr(settings, "S3_KMS_KEY_ID", None)
        if kms_key_id:
            extra_args["SSEKMSKeyId"] = kms_key_id
        self._client.put_object(Bucket=self.bucket_name, Key=object_key, Body=data, **extra_args)
        return object_key

    async def upload_path(self, key: str, path: str, content_type: Optional[str] = None) -> str:
        object_key = self._object_key(key)
        extra_args = {}
        if content_type:
            extra_args["ContentType"] = content_type
        sse = getattr(settings, "AWS_S3_SERVER_SIDE_ENCRYPTION", None) or getattr(settings, "S3_SERVER_SIDE_ENCRYPTION", None)
        if sse:
            extra_args["ServerSideEncryption"] = sse
        kms_key_id = getattr(settings, "AWS_S3_KMS_KEY_ID", None) or getattr(settings, "S3_KMS_KEY_ID", None)
        if kms_key_id:
            extra_args["SSEKMSKeyId"] = kms_key_id
        kwargs = {
            "Filename": str(path),
            "Bucket": self.bucket_name,
            "Key": object_key,
        }
        if extra_args:
            kwargs["ExtraArgs"] = extra_args
        self._client.upload_file(**kwargs)
        return object_key

    async def upload_file(
        self,
        file,
        name: str,
        path: str,
        organization_id: str,
        project_id: Optional[str],
    ) -> str:
        # Backward compatibility wrapper; expects file to be a file-like with read()
        data = file.read() if hasattr(file, "read") else file
        key_prefix = path.strip("/").rstrip("/")
        object_key = f"{organization_id}/{project_id or 'shared'}/{key_prefix}/{name}"
        return await self.upload_bytes(object_key, data)

    async def cleanup_deleted_items(self, path: str) -> None:
        # Placeholder cleanup routine.
        return None

    async def download_bytes(self, key: str) -> bytes:
        """
        Download an object and return its bytes. Caller decides how to persist/stream.
        """
        object_key = self._object_key(key)
        response = self._client.get_object(Bucket=self.bucket_name, Key=object_key)
        body = response.get("Body")
        if body is None:
            return b""
        return body.read()

    async def generate_presigned_url(
        self, file_path: str, current_user: Any, expire_minutes: int = 60
    ) -> dict:
        object_key = self._object_key(file_path)
        expires_at = datetime.utcnow() + timedelta(minutes=expire_minutes)
        url = self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket_name, "Key": object_key},
            ExpiresIn=int(expire_minutes * 60),
        )
        return {
            "url": url,
            "expires_at": expires_at.isoformat(),
        }
