"""boto3 against a MinIO (or AWS) endpoint, made awaitable.

boto3 is synchronous, so every call goes through `asyncio.to_thread`: the event
loop that is also serving HTTP and polling Telegram must never block on a socket
to the object store. aiobotocore would avoid the thread hop, but it is not
installable on the build machine (see the offline amendment in
`docs/superpowers/plans/2026-09-15-wave3a-queue-blobs-secrets.md`), and a thread
per call is cheap next to the round trip it is waiting on.

A boto3 *client* — unlike a boto3 resource — is documented as thread-safe once
created, and the creation itself happens on the event-loop thread, so there is
exactly one client per process and no lock around it.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import boto3
import structlog
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from claude_hermes.core.exceptions import NotFound

log = structlog.get_logger(__name__)

# The key does not exist, spelled three different ways depending on which call
# asked and which S3 implementation answered.
_MISSING = {"404", "NoSuchKey", "NoSuchBucket"}


class S3Client:
    """put/get/upload/download/delete/list over one bucket."""

    def __init__(self, endpoint: str, access_key: str, secret_key: str, bucket: str,
                 region: str = "us-east-1") -> None:
        self.endpoint = endpoint
        self.bucket = bucket
        self.region = region
        self._access_key = access_key
        self._secret_key = secret_key
        self._client: Any | None = None

    def _boto(self) -> Any:
        """Lazily built, on the event-loop thread. Nothing is connected here —
        boto3 does not open a socket until the first call — so this is safe to
        run at wiring time even when MinIO is not up yet."""
        if self._client is None:
            self._client = boto3.client(
                "s3",
                endpoint_url=self.endpoint or None,
                aws_access_key_id=self._access_key or None,
                aws_secret_access_key=self._secret_key or None,
                region_name=self.region,
                # path style: `http://minio:9000/hermes/key`, not
                # `http://hermes.minio:9000/key` — the virtual-host form needs DNS
                # for every bucket name and does not exist on a compose network.
                config=BotoConfig(s3={"addressing_style": "path"},
                                  retries={"max_attempts": 3, "mode": "standard"}),
            )
        return self._client

    async def _call(self, op: str, **kwargs: Any) -> Any:
        client = self._boto()
        return await asyncio.to_thread(getattr(client, op), **kwargs)

    async def ensure_bucket(self) -> None:
        """Idempotent: safe to call from every process at every boot.

        `head_bucket` first so the normal path is one cheap request, and the
        create races are swallowed — N workers booting together all try, one
        wins, and the losers must not crash the boot.
        """
        try:
            await self._call("head_bucket", Bucket=self.bucket)
            return
        except ClientError as e:
            if _code(e) not in _MISSING:
                raise
        kwargs: dict[str, Any] = {"Bucket": self.bucket}
        # us-east-1 is the one region where sending a LocationConstraint is an
        # error rather than a no-op.
        if self.region and self.region != "us-east-1":
            kwargs["CreateBucketConfiguration"] = {"LocationConstraint": self.region}
        try:
            await self._call("create_bucket", **kwargs)
        except ClientError as e:
            if _code(e) not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                raise
        log.info("s3_bucket_ready", bucket=self.bucket, endpoint=self.endpoint)

    async def put_object(self, key: str, data: bytes) -> None:
        await self._call("put_object", Bucket=self.bucket, Key=key, Body=data)

    async def get_object(self, key: str) -> bytes:
        try:
            response = await self._call("get_object", Bucket=self.bucket, Key=key)
        except ClientError as e:
            if _code(e) in _MISSING:
                raise NotFound(f"no object at {key}") from None
            raise
        # .read() is a socket read, so it belongs in a thread too.
        return await asyncio.to_thread(response["Body"].read)

    async def upload_file(self, key: str, path: str | Path) -> None:
        await self._call("upload_file", Bucket=self.bucket, Key=key, Filename=str(path))

    async def download_file(self, key: str, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            await self._call("download_file", Bucket=self.bucket, Key=key,
                             Filename=str(target))
        except ClientError as e:
            if _code(e) in _MISSING:
                raise NotFound(f"no object at {key}") from None
            raise

    async def delete_object(self, key: str) -> None:
        # S3 delete is already idempotent — deleting a key that is not there is
        # a 204, not an error.
        await self._call("delete_object", Bucket=self.bucket, Key=key)

    async def list_prefix(self, prefix: str) -> list[str]:
        keys: list[str] = []
        token: str | None = None
        while True:
            kwargs: dict[str, Any] = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            page = await self._call("list_objects_v2", **kwargs)
            keys.extend(item["Key"] for item in page.get("Contents", ()))
            # A bucket with more than 1000 matching keys comes back truncated;
            # without this loop a lane would silently list only its first page.
            token = page.get("NextContinuationToken")
            # `IsTruncated` without a token would loop forever re-reading page
            # one; treat a missing token as the end whatever the flag says.
            if not page.get("IsTruncated") or not token:
                return keys

    async def aclose(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await asyncio.to_thread(client.close)


def _code(error: ClientError) -> str:
    response = getattr(error, "response", None) or {}
    err = response.get("Error") or {}
    return str(err.get("Code") or response.get("ResponseMetadata", {}).get("HTTPStatusCode", ""))
