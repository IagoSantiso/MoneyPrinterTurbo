"""Real TV product photos/videos, fetched from Cloudflare R2.

Each TV has a folder-like key prefix in one R2 bucket (e.g.
``SAMSUNG_QN90D_55/``), matching ``TVSpecs.product_images_prefix``. Inside
that prefix. Filenames don't need a special convention — plain camera
exports (``IMG_2087.jpg``...) work fine, since a leading number (if any)
is used for ordering but isn't required. The pipeline doesn't depend on a
fixed photo order: each photo becomes its own Ken Burns clip and
``combine_videos`` decides duration/transitions per clip to fit the
script's length.

Two ways to resolve a prefix into a list of downloadable files, both
implemented here:

- ``api`` (recommended, default): calls R2's S3-compatible API with a
  scoped Access Key ID/Secret Access Key to list objects under the prefix.
  Works regardless of whether the bucket has public access, gives an exact
  object list (no guessing at filenames/extensions), and is what the
  ``upload_tv_assets.py`` script also uses to upload.
- ``public_url``: skips the API entirely and HEAD-probes a small set of
  conventional filenames under a public bucket domain
  (``https://<public_base_url>/<prefix><NN>.<ext>``). Simpler to set up (no
  API credentials needed at read time) but only works if the bucket has a
  public domain enabled, and can miss files that don't match the guessed
  naming pattern — prefer ``api`` unless you specifically don't want to
  hand out R2 credentials to whatever runs the pipeline.

Fallback contract (required by the macroprompt): a missing/blank prefix,
or a prefix with zero resolved files, must return an empty list rather
than raising, so callers fall back to generic stock footage without
breaking the run.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from loguru import logger

from app.utils import utils

_NUMERIC_PREFIX_RE = re.compile(r"^(\d+)")
_PROBE_INDICES = range(1, 21)  # 01..20 — generous upper bound for a HEAD probe
_PROBE_EXTENSIONS = ("jpg", "jpeg", "png", "webp", "mp4", "mov")


class R2NotConfiguredError(RuntimeError):
    """Raised when the api method is requested without R2 credentials."""


def _r2_config(app_config=None) -> dict:
    from app.config import config as app_config_module

    cfg = app_config or app_config_module
    return cfg.r2


def get_r2_client(app_config=None):
    """Builds a boto3 S3 client pointed at the configured R2 account."""
    import boto3

    r2 = _r2_config(app_config)
    account_id = r2.get("account_id", "")
    access_key_id = r2.get("access_key_id", "")
    secret_access_key = r2.get("secret_access_key", "")

    if not (account_id and access_key_id and secret_access_key):
        raise R2NotConfiguredError(
            "R2 is not configured: set [r2] account_id, access_key_id, and "
            "secret_access_key in config.toml"
        )

    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key_id,
        aws_secret_access_key=secret_access_key,
        region_name="auto",
    )


def _sort_key(object_key: str) -> tuple:
    """Numeric-prefix-aware sort so 02_x sorts before 10_x (not after)."""
    basename = object_key.rsplit("/", 1)[-1]
    match = _NUMERIC_PREFIX_RE.match(basename)
    return (int(match.group(1)) if match else 10**9, basename)


def list_product_media_keys_via_api(prefix: str, app_config=None) -> list[str]:
    """Lists object keys under ``prefix`` via the R2 S3-compatible API,
    sorted by leading filename number when present (01_, IMG_2087, ...),
    otherwise alphabetically — a convenience, not a requirement.

    Returns an empty list — never raises — for a blank prefix or a bucket
    with nothing under that prefix, so the caller can fall back to stock
    footage. Raises R2NotConfiguredError only if credentials are missing,
    since that's a setup error the caller should surface, not silently
    swallow.
    """
    prefix = (prefix or "").strip()
    if not prefix:
        return []

    r2 = _r2_config(app_config)
    bucket_name = r2.get("bucket_name", "")
    if not bucket_name:
        raise R2NotConfiguredError("R2 is not configured: set [r2] bucket_name")

    client = get_r2_client(app_config)
    keys: list[str] = []
    continuation_token: Optional[str] = None
    while True:
        kwargs = {"Bucket": bucket_name, "Prefix": prefix}
        if continuation_token:
            kwargs["ContinuationToken"] = continuation_token
        response = client.list_objects_v2(**kwargs)
        for obj in response.get("Contents", []):
            key = obj["Key"]
            if not key.endswith("/"):  # skip the folder placeholder object, if any
                keys.append(key)
        if not response.get("IsTruncated"):
            break
        continuation_token = response.get("NextContinuationToken")

    keys.sort(key=_sort_key)
    logger.info(f"R2: found {len(keys)} object(s) under prefix {prefix!r}")
    return keys


def list_product_media_urls_via_public_probe(
    prefix: str, public_base_url: str
) -> list[str]:
    """HEAD-probes conventional filenames (01.jpg, 02.jpg, ...) under a
    public R2 bucket domain. See module docstring for when to prefer this
    over the API method. Returns [] for a blank prefix/base URL, never
    raises on individual probe failures (a 404 just means "not this one").
    """
    import requests

    prefix = (prefix or "").strip()
    public_base_url = (public_base_url or "").strip().rstrip("/")
    if not prefix or not public_base_url:
        return []

    found_urls = []
    for index in _PROBE_INDICES:
        for ext in _PROBE_EXTENSIONS:
            url = f"{public_base_url}/{prefix.rstrip('/')}/{index:02d}.{ext}"
            try:
                resp = requests.head(url, timeout=5)
            except requests.RequestException:
                continue
            if resp.status_code == 200:
                found_urls.append(url)
                break  # found this index's file, try the next index
    logger.info(
        f"R2 public probe: found {len(found_urls)} file(s) under prefix {prefix!r}"
    )
    return found_urls


def download_and_cache_product_media(
    prefix: str, method: str = "api", app_config=None
) -> list[Path]:
    """Resolves ``prefix`` to files and downloads/caches them locally,
    returning local paths ready to hand to the video pipeline as
    ``MaterialInfo(provider="local", url=<path>)``.

    Always returns [] (never raises) when the prefix is blank or resolves
    to zero files — that is the documented fallback-to-stock signal.
    Raises R2NotConfiguredError only for a genuine setup problem (method
    "api" without credentials), so a misconfiguration is loud instead of
    silently producing an empty video.
    """
    prefix = (prefix or "").strip()
    if not prefix:
        return []

    cache_root = utils.storage_dir("tv_product_media_cache", create=True)
    prefix_cache_dir = Path(cache_root) / prefix.strip("/")

    if method == "public_url":
        r2 = _r2_config(app_config)
        urls = list_product_media_urls_via_public_probe(
            prefix, r2.get("public_base_url", "")
        )
        if not urls:
            return []
        import requests

        prefix_cache_dir.mkdir(parents=True, exist_ok=True)
        local_paths = []
        for url in urls:
            filename = url.rsplit("/", 1)[-1]
            local_path = prefix_cache_dir / filename
            if not local_path.exists():
                resp = requests.get(url, timeout=30)
                resp.raise_for_status()
                local_path.write_bytes(resp.content)
            local_paths.append(local_path)
        return local_paths

    # method == "api" (default)
    keys = list_product_media_keys_via_api(prefix, app_config)
    if not keys:
        return []

    r2 = _r2_config(app_config)
    bucket_name = r2.get("bucket_name", "")
    client = get_r2_client(app_config)

    prefix_cache_dir.mkdir(parents=True, exist_ok=True)
    local_paths = []
    for key in keys:
        filename = key.rsplit("/", 1)[-1]
        local_path = prefix_cache_dir / filename
        if not local_path.exists():
            client.download_file(bucket_name, key, str(local_path))
        local_paths.append(local_path)

    logger.info(
        f"R2: cached {len(local_paths)} file(s) for prefix {prefix!r} "
        f"under {prefix_cache_dir}"
    )
    return local_paths
