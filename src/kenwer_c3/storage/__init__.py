"""Object storage adapters (MinIO/S3) — optional for core memory path."""

from kenwer_c3.storage.s3 import ObjectStorageClient, ObjectStorageError

__all__ = ["ObjectStorageClient", "ObjectStorageError"]
