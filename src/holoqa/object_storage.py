from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def enabled() -> bool:
    return os.environ.get("HOLOQA_OBJECT_STORAGE", "local").lower() == "s3"


def _client(endpoint: str | None = None):
    import boto3
    return boto3.client(
        "s3",
        endpoint_url=endpoint or os.environ.get("HOLOQA_S3_ENDPOINT", "http://garage:3900"),
        region_name=os.environ.get("HOLOQA_S3_REGION", "garage"),
        aws_access_key_id=os.environ.get("HOLOQA_S3_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("HOLOQA_S3_SECRET_ACCESS_KEY"),
    )


def bucket() -> str:
    return os.environ.get("HOLOQA_S3_BUCKET", "holoqa-artifacts")


def upload_file(path: Path, key: str) -> str:
    if not enabled():
        return str(path)
    _client().upload_file(str(path), bucket(), key)
    return f"s3://{bucket()}/{key}"


def prepare_upload(key: str, content_type: str = "application/octet-stream", expires_in: int = 900) -> dict[str, str | int]:
    """Create a short-lived upload URL for an agent-owned artifact."""
    if not enabled():
        raise RuntimeError("Garage object storage must be enabled for agent artifact uploads")
    public_endpoint = os.environ.get("HOLOQA_S3_PUBLIC_ENDPOINT")
    if not public_endpoint:
        raise RuntimeError("HOLOQA_S3_PUBLIC_ENDPOINT is required for remote agent artifact uploads")
    url = _client(public_endpoint).generate_presigned_url(
        "put_object",
        Params={"Bucket": bucket(), "Key": key, "ContentType": content_type},
        ExpiresIn=expires_in,
        HttpMethod="PUT",
    )
    return {"upload_url": url, "artifact_uri": f"s3://{bucket()}/{key}", "expires_in": expires_in}


def artifact_exists(uri: str) -> bool:
    if not uri.startswith("s3://"):
        return False
    _, location = uri.split("s3://", 1)
    bucket_name, key = location.split("/", 1)
    try:
        _client().head_object(Bucket=bucket_name, Key=key)
        return True
    except Exception:
        return False


def download(path: str) -> tuple[bytes, str]:
    if not path.startswith("s3://"):
        return Path(path).read_bytes(), "application/octet-stream"
    _, location = path.split("s3://", 1)
    bucket_name, key = location.split("/", 1)
    response = _client().get_object(Bucket=bucket_name, Key=key)
    return response["Body"].read(), response.get("ContentType", "application/octet-stream")


def offload_results(results: list[dict[str, Any]], run_id: str) -> list[dict[str, Any]]:
    if not enabled():
        return results
    for result in results:
        test_id = str(result.get("test_id") or "unknown")
        items = result.get("evidence_items") or []
        for item in items:
            path = Path(str(item.get("path") or ""))
            if path.is_file():
                item["path"] = upload_file(path, f"runs/{run_id}/{test_id}/{path.name}")
                path.unlink(missing_ok=True)
        result["evidence"] = ";".join(str(item.get("path")) for item in items if item.get("path"))
    return results
