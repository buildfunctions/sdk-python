"""Model - Upload and manage models independently from GPU Sandboxes."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

import httpx

from buildfunctions.dotdict import DotDict
from buildfunctions.errors import BuildfunctionsError, ValidationError
from buildfunctions.uploader import get_files_in_directory, upload_model_files

DEFAULT_BASE_URL = "https://www.buildfunctions.com"

# Module-level state
_global_api_token: str | None = None
_global_base_url: str | None = None
def set_model_api_token(
    api_token: str,
    base_url: str | None = None,
    user_id: str | None = None,  # noqa: ARG001
    username: str | None = None,  # noqa: ARG001
) -> None:
    """Set the API token for Model operations."""
    global _global_api_token, _global_base_url
    _global_api_token = api_token
    _global_base_url = base_url


def _sanitize_model_name(name: str) -> str:
    result = name.lower()
    result = unicodedata.normalize("NFD", result)
    result = re.sub(r"[\u0300-\u036f]", "", result)
    result = result.strip()
    result = result.replace("&", "-and-")
    result = re.sub(r"[^a-z0-9 -]", "", result)
    result = re.sub(r"\s+", "-", result)
    result = re.sub(r"-+", "-", result)
    return result


async def _create_model(config: dict[str, Any]) -> DotDict:
    """Create a model by uploading it to cloud storage."""
    if not _global_api_token:
        raise ValidationError("API key not set. Initialize Buildfunctions client first.")

    base_url = _global_base_url or DEFAULT_BASE_URL
    model_path_str = config.get("path")

    if not model_path_str:
        raise ValidationError("Model path is required")

    model_path = Path(model_path_str)
    if not model_path.exists():
        raise ValidationError(f"Model path does not exist: {model_path_str}")

    if not model_path.is_dir():
        raise ValidationError("Model path must be a directory")

    local_upload_file_name = model_path.name
    model_name = config.get("name") or _sanitize_model_name(local_upload_file_name)

    # Validate model name
    if not re.match(r"^[a-z0-9-]+$", model_name):
        raise ValidationError("Model name must contain only lowercase letters, numbers, and hyphens")

    print(f'   Creating model "{model_name}" from {model_path_str}...')

    # Collect files
    files = get_files_in_directory(model_path_str)
    if not files:
        raise ValidationError("No files found in model directory")

    print(f"   Found {len(files)} files to upload")

    files_within_model_folder = [
        {
            "name": f["name"],
            "size": f["size"],
            "type": f["type"],
            "webkitRelativePath": f["webkit_relative_path"],
        }
        for f in files
    ]

    # POST to model/create endpoint
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
        response = await client.post(
            f"{base_url}/api/sdk/model/create",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {_global_api_token}",
            },
            json={
                "modelName": model_name,
                "localUploadFileName": local_upload_file_name,
                "filesWithinModelFolder": files_within_model_folder,
            },
        )

    if not response.is_success:
        raise BuildfunctionsError(
            f"Failed to create model: {response.text}", "UNKNOWN_ERROR", response.status_code
        )

    data = response.json()

    # Upload files to S3
    model_presigned = data.get("modelPresignedUrls")
    if model_presigned:
        print("   Uploading model files to S3...")
        await upload_model_files(
            files,
            model_presigned,
            data.get("bucketName", ""),
            base_url,
        )
        print("   Model files uploaded successfully")

    model_id = data["modelId"]
    final_model_name = data["modelName"]

    async def delete_fn() -> None:
        await _delete_model({"where": {"name": final_model_name}})

    return DotDict({
        "id": model_id,
        "name": final_model_name,
        "delete": delete_fn,
    })


async def _find_unique_model(options: dict[str, Any]) -> DotDict | None:
    """Find a model by name or id, scoped to the authenticated user."""
    if not _global_api_token:
        raise ValidationError("API key not set. Initialize Buildfunctions client first.")

    base_url = _global_base_url or DEFAULT_BASE_URL
    where = options.get("where", {})
    params = {}
    if where.get("name"):
        params["name"] = where["name"]
    if where.get("id"):
        params["id"] = where["id"]

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        response = await client.get(
            f"{base_url}/api/sdk/model/find",
            params=params,
            headers={
                "Authorization": f"Bearer {_global_api_token}",
            },
        )

    if response.status_code == 404:
        return None

    if not response.is_success:
        raise BuildfunctionsError(
            f"Failed to find model: {response.text}", "UNKNOWN_ERROR", response.status_code
        )

    data = response.json()
    model_name = data["modelName"]

    async def delete_fn() -> None:
        await _delete_model({"where": {"name": model_name}})

    return DotDict({
        "id": data["modelId"],
        "name": model_name,
        "delete": delete_fn,
    })


async def _delete_model(options: dict[str, Any]) -> None:
    """Delete a model by name or id."""
    if not _global_api_token:
        raise ValidationError("API key not set. Initialize Buildfunctions client first.")

    base_url = _global_base_url or DEFAULT_BASE_URL
    where = options.get("where", {})
    model_name = where.get("name") or where.get("id")

    if not model_name:
        raise ValidationError("Model name or id is required")

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        response = await client.request(
            "DELETE",
            f"{base_url}/api/sdk/model/delete",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {_global_api_token}",
            },
            json={"modelName": model_name},
        )

    if not response.is_success:
        raise BuildfunctionsError(
            f"Failed to delete model: {response.text}", "UNKNOWN_ERROR", response.status_code
        )


class Model:
    """Model factory - upload and manage models independently from GPU Sandboxes."""

    @staticmethod
    async def create(config: dict[str, Any]) -> DotDict:
        """Create a model by uploading it to cloud storage."""
        return await _create_model(config)

    @staticmethod
    async def findUnique(options: dict[str, Any]) -> DotDict | None:
        """Find a model by name or id, scoped to the authenticated user."""
        return await _find_unique_model(options)

    @staticmethod
    async def find_unique(options: dict[str, Any]) -> DotDict | None:
        """Find a model by name or id (snake_case alias)."""
        return await _find_unique_model(options)

    @staticmethod
    async def delete(config: dict[str, Any]) -> None:
        """Delete a model and all its associated resources."""
        await _delete_model(config)


# Alias for direct function call style
create_model = _create_model
