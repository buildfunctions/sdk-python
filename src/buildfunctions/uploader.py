"""File upload utilities for GPU Sandbox."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx

from buildfunctions.types import FileMetadata, PresignedUrlInfo

CHUNK_SIZE = 9 * 1024 * 1024  # 9MB
MAX_PARALLEL_UPLOADS = 5


async def upload_file(content: bytes, presigned_url: str) -> None:
    """Upload a single file to a presigned URL."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(600.0)) as client:
        response = await client.put(
            presigned_url,
            content=content,
            headers={"Content-Type": "application/octet-stream"},
        )
    if not response.is_success:
        raise RuntimeError(f"Failed to upload file: {response.reason_phrase}")


async def upload_part(content: bytes, presigned_url: str, part_number: int) -> dict[str, Any]:
    """Upload a single part of a multipart upload."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(600.0)) as client:
        response = await client.put(
            presigned_url,
            content=content,
            headers={"Content-Type": "application/octet-stream"},
        )

    if not response.is_success:
        raise RuntimeError(f"Failed to upload part {part_number}: {response.reason_phrase}")

    etag = response.headers.get("ETag")
    if not etag:
        raise RuntimeError(f"Failed to retrieve ETag for part {part_number}")

    clean_etag = etag.strip('"')
    return {"PartNumber": part_number, "ETag": clean_etag}


def _read_chunk(file_path: str, start: int, end: int) -> bytes:
    """Read a chunk of a file from disk without loading the entire file."""
    with open(file_path, "rb") as f:
        f.seek(start)
        return f.read(end - start)


async def upload_multipart_file(
    file_path: str,
    file_size: int,
    signed_urls: list[str],
    upload_id: str,
    number_of_parts: int,
    bucket_name: str,
    s3_file_path: str,
    base_url: str,
) -> None:
    """Orchestrate a multipart upload with parallel chunk uploads."""
    parts: list[dict[str, Any]] = []
    semaphore = asyncio.Semaphore(MAX_PARALLEL_UPLOADS)

    async def _upload_chunk(index: int) -> None:
        async with semaphore:
            part_number = index + 1
            start = index * CHUNK_SIZE
            end = min(start + CHUNK_SIZE, file_size)
            chunk = _read_chunk(file_path, start, end)
            url = signed_urls[index]
            if not url:
                raise RuntimeError(f"Missing upload URL for part {part_number}")
            part = await upload_part(chunk, url, part_number)
            parts.append(part)

    tasks = [_upload_chunk(i) for i in range(number_of_parts)]
    await asyncio.gather(*tasks)

    sorted_parts = sorted(parts, key=lambda p: p["PartNumber"])

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        response = await client.post(
            f"{base_url}/api/functions/gpu/transfer-and-mount/complete-multipart-upload",
            json={
                "bucketName": bucket_name,
                "uploadId": upload_id,
                "parts": sorted_parts,
                "s3FilePath": s3_file_path,
                "fileName": s3_file_path.split("/")[-1] if "/" in s3_file_path else s3_file_path,
            },
        )

    if not response.is_success:
        error_text = response.text
        raise RuntimeError(f"Failed to complete upload: {response.reason_phrase} - {error_text}")


def get_files_in_directory(dir_path: str) -> list[FileMetadata]:
    """Recursively walk a directory and collect file metadata."""
    root = Path(dir_path)
    root_dir_name = root.name
    files: list[FileMetadata] = []

    for file_path in root.rglob("*"):
        if file_path.is_file():
            relative = file_path.relative_to(root)
            files.append(
                FileMetadata(
                    name=file_path.name,
                    size=file_path.stat().st_size,
                    type="application/octet-stream",
                    webkit_relative_path=f"{root_dir_name}/{relative}",
                    local_path=str(file_path),
                )
            )

    return files


class UploadProgress:
    """Tracks upload progress for display."""
    __slots__ = ("total_files", "completed_files", "total_bytes", "uploaded_bytes", "skipped_files", "start_time")

    def __init__(self, total_files: int, total_bytes: int, skipped_files: int = 0) -> None:
        self.total_files = total_files
        self.completed_files = 0
        self.total_bytes = total_bytes
        self.uploaded_bytes = 0
        self.skipped_files = skipped_files
        self.start_time = asyncio.get_event_loop().time()


async def upload_model_files(
    files: list[FileMetadata],
    presigned_urls: dict[str, PresignedUrlInfo],
    bucket_name: str,
    base_url: str,
    on_progress: Any | None = None,
) -> None:
    """Upload all model files using presigned URLs."""
    files_to_upload: list[tuple[FileMetadata, PresignedUrlInfo]] = []

    for file in files:
        url_info = presigned_urls.get(file["webkit_relative_path"])
        if not url_info:
            continue
        files_to_upload.append((file, url_info))

    skipped = len(files) - len(files_to_upload)
    total_bytes = sum(f["size"] for f, _ in files_to_upload)
    progress = UploadProgress(len(files_to_upload), total_bytes, skipped)

    if on_progress:
        on_progress(progress)

    upload_tasks: list[asyncio.Task[None]] = []

    for file, url_info in files_to_upload:
        signed_urls = url_info["signedUrl"]

        async def _upload(f: FileMetadata = file, urls: list[str] = signed_urls, ui: PresignedUrlInfo = url_info) -> None:
            if len(urls) > 1 and ui.get("uploadId"):
                await upload_multipart_file(
                    f["local_path"],
                    f["size"],
                    urls,
                    ui["uploadId"],  # type: ignore[arg-type]
                    ui.get("numberOfParts", len(urls)),
                    bucket_name,
                    ui.get("s3FilePath", ""),
                    base_url,
                )
            elif len(urls) == 1 and urls[0]:
                content = Path(f["local_path"]).read_bytes()
                await upload_file(content, urls[0])
            progress.completed_files += 1
            progress.uploaded_bytes += f["size"]
            if on_progress:
                on_progress(progress)

        upload_tasks.append(asyncio.ensure_future(_upload()))

    if upload_tasks:
        await asyncio.gather(*upload_tasks)


async def transfer_files_to_storage(
    files: list[FileMetadata],
    sanitized_model_name: str,
    base_url: str,
    session_token: str,
) -> None:
    """Transfer files to persistent storage."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
        details_response = await client.post(
            f"{base_url}/api/sdk/sandbox/gpu/get-transfer-details",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {session_token}",
            },
            json={
                "shouldVerifyContents": False,
                "filesToTransfer": [f["webkit_relative_path"] for f in files],
                "sanitizedModelName": sanitized_model_name,
                "fileNamesWithinModelFolder": [f["name"] for f in files],
            },
        )

    if not details_response.is_success:
        error_data = details_response.json()
        raise RuntimeError(error_data.get("error", "Failed to prepare file transfer"))

    transfer_data = details_response.json()
    transfer_details = transfer_data["transferDetails"]
    storage_api_url = transfer_data["storageApiUrl"]
    storage_api_path = transfer_data["storageApiPath"]

    valid_details = [d for d in transfer_details if d.get("fileName")]

    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
        for file_detail in valid_details:
            response = await client.post(
                f"{storage_api_url}{storage_api_path}",
                json=file_detail,
            )
            if not response.is_success:
                error_text = response.text
                raise RuntimeError(f"Failed to transfer {file_detail['fileName']}: {error_text}")
