"""The S3 adapter: an object store (MinIO in this deployment) behind seven calls.

Everything here is generic S3 — the bucket could be AWS tomorrow. The `files`
module's `S3BlobStore` is what knows about inbox/outbox keys and local mirrors;
this package knows only about keys and bytes.
"""
from claude_hermes.integrations.s3.client import S3Client

__all__ = ["S3Client"]
