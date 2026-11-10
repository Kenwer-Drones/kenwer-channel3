"""S3/MinIO client for evidence blobs (local-dev).

Evidence object storage is optional for the Cognitive Identity Layer core path.
Authoritative mission knowledge remains in PostgreSQL.
"""

from __future__ import annotations

import logging
from typing import Any

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from kenwer_c3.settings import Settings, get_settings

logger = logging.getLogger(__name__)


class ObjectStorageError(Exception):
    pass


class ObjectStorageClient:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._client = boto3.client(
            "s3",
            endpoint_url=self.settings.s3_endpoint_url,
            aws_access_key_id=self.settings.s3_access_key,
            aws_secret_access_key=self.settings.s3_secret_key,
            region_name=self.settings.s3_region,
            config=Config(signature_version="s3v4"),
        )

    def ensure_bucket(self) -> dict[str, Any]:
        bucket = self.settings.s3_bucket
        try:
            self._client.head_bucket(Bucket=bucket)
            return {"bucket": bucket, "exists": True, "created": False}
        except ClientError:
            try:
                self._client.create_bucket(Bucket=bucket)
                return {"bucket": bucket, "exists": True, "created": True}
            except (ClientError, BotoCoreError) as exc:
                raise ObjectStorageError(f"Cannot create bucket {bucket}: {exc}") from exc

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        bucket = self.settings.s3_bucket
        try:
            self._client.put_object(
                Bucket=bucket, Key=key, Body=data, ContentType=content_type
            )
        except (ClientError, BotoCoreError) as exc:
            raise ObjectStorageError(f"Upload failed: {exc}") from exc
        return f"s3://{bucket}/{key}"

    def get_bytes(self, key: str) -> bytes:
        bucket = self.settings.s3_bucket
        try:
            obj = self._client.get_object(Bucket=bucket, Key=key)
            return obj["Body"].read()
        except (ClientError, BotoCoreError) as exc:
            raise ObjectStorageError(f"Download failed: {exc}") from exc

    def ping(self) -> bool:
        try:
            self._client.list_buckets()
            return True
        except (ClientError, BotoCoreError):
            return False
