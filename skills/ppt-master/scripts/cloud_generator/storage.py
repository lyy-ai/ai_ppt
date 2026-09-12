#!/usr/bin/env python3
"""Artifact storage helpers for the cloud generator API.

Default mode is local API serving. S3-compatible mode is optional and supports
Tencent COS, Cloudflare R2, MinIO, and AWS S3 when boto3 is installed.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StoredArtifact:
    storage: str
    url: str
    remote_key: str = ""
    signed: bool = False
    expires_at: int | None = None


def storage_mode() -> str:
    return os.environ.get("PPT_MASTER_STORAGE_MODE", "local").strip().lower() or "local"


def publish_artifact(*, task_id: str, kind: str, path: Path, local_url: str) -> StoredArtifact:
    """Return a browser URL for an artifact, uploading it when configured."""
    mode = storage_mode()
    if mode in {"local", "api", "json"}:
        return StoredArtifact(storage="local", url=local_url)
    if mode in {"s3", "cos", "r2", "minio"}:
        return _publish_s3(task_id=task_id, kind=kind, path=path)
    raise RuntimeError(f"Unsupported PPT_MASTER_STORAGE_MODE: {mode}")


def _publish_s3(*, task_id: str, kind: str, path: Path) -> StoredArtifact:
    bucket = _env("S3_BUCKET", "PPT_MASTER_S3_BUCKET", "COS_BUCKET", "R2_BUCKET")
    if not bucket:
        raise RuntimeError("S3 storage requires S3_BUCKET or PPT_MASTER_S3_BUCKET")
    prefix = _env("S3_PREFIX", "PPT_MASTER_S3_PREFIX") or "ppt-master/artifacts"
    key = f"{prefix.strip('/')}/{_safe_segment(task_id)}/{_safe_segment(kind)}/{path.name}"
    client = _s3_client()
    content_type = _content_type(path)
    client.upload_file(
        str(path),
        bucket,
        key,
        ExtraArgs={"ContentType": content_type},
    )
    public_base = _env("S3_PUBLIC_BASE_URL", "PPT_MASTER_S3_PUBLIC_BASE_URL", "COS_PUBLIC_BASE_URL", "R2_PUBLIC_BASE_URL")
    if public_base:
        return StoredArtifact(
            storage=storage_mode(),
            url=f"{public_base.rstrip('/')}/{key}",
            remote_key=key,
            signed=False,
        )
    expires = int(_env("S3_PRESIGN_EXPIRES", "PPT_MASTER_S3_PRESIGN_EXPIRES") or "3600")
    url = client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expires,
    )
    return StoredArtifact(
        storage=storage_mode(),
        url=url,
        remote_key=key,
        signed=True,
        expires_at=int(time.time()) + expires,
    )


def _s3_client():
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError("S3 storage requires boto3. Install it with: pip install boto3") from exc

    access_key = _env("S3_ACCESS_KEY_ID", "AWS_ACCESS_KEY_ID", "COS_SECRET_ID", "R2_ACCESS_KEY_ID")
    secret_key = _env("S3_SECRET_ACCESS_KEY", "AWS_SECRET_ACCESS_KEY", "COS_SECRET_KEY", "R2_SECRET_ACCESS_KEY")
    endpoint = _env("S3_ENDPOINT_URL", "AWS_ENDPOINT_URL", "COS_ENDPOINT_URL", "R2_ENDPOINT_URL")
    region = _env("S3_REGION", "AWS_REGION", "COS_REGION", "R2_REGION") or "auto"
    kwargs: dict[str, Any] = {"region_name": region}
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    if access_key and secret_key:
        kwargs["aws_access_key_id"] = access_key
        kwargs["aws_secret_access_key"] = secret_key
    return boto3.client("s3", **kwargs)


def _env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _safe_segment(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in str(value or "artifact"))[:160] or "artifact"


def _content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".svg":
        return "image/svg+xml; charset=utf-8"
    if suffix == ".mp3":
        return "audio/mpeg"
    if suffix == ".wav":
        return "audio/wav"
    if suffix == ".m4a":
        return "audio/mp4"
    if suffix == ".pptx":
        return "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    if suffix == ".png":
        return "image/png"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    if suffix == ".gif":
        return "image/gif"
    return "application/octet-stream"
