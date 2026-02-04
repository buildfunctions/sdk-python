"""GPU Sandbox - Hardware-isolated execution environment with GPU acceleration."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

import httpx

from buildfunctions.dotdict import DotDict
from buildfunctions.errors import BuildfunctionsError, ValidationError
from buildfunctions.framework import detect_framework
from buildfunctions.memory import parse_memory
from buildfunctions.resolve_code import resolve_code
from buildfunctions.types import (
    FileMetadata,
    GPUSandboxConfig,
    GPUSandboxInstance,
    GPUType,
    RunResult,
    UploadOptions,
)
from buildfunctions.uploader import get_files_in_directory, upload_model_files

DEFAULT_GPU_BUILD_URL = "https://prod-gpu-build.buildfunctions.link"
DEFAULT_BASE_URL = "https://www.buildfunctions.com"

# Module-level state
_global_api_token: str | None = None
_global_gpu_build_url: str | None = None
_global_base_url: str | None = None
_global_user_id: str | None = None
_global_username: str | None = None
_global_compute_tier: str | None = None


def set_gpu_sandbox_api_token(
    api_token: str,
    gpu_build_url: str | None = None,
    user_id: str | None = None,
    username: str | None = None,
    compute_tier: str | None = None,
    base_url: str | None = None,
) -> None:
    """Set the API token for GPU Sandbox operations."""
    global _global_api_token, _global_gpu_build_url, _global_user_id
    global _global_username, _global_compute_tier, _global_base_url
    _global_api_token = api_token
    _global_gpu_build_url = gpu_build_url
    _global_user_id = user_id
    _global_username = username
    _global_compute_tier = compute_tier
    _global_base_url = base_url


def _validate_config(config: GPUSandboxConfig) -> None:
    name = config.get("name")
    if not name or not isinstance(name, str):
        raise ValidationError("Sandbox name is required")

    language = config.get("language")
    if not language or not isinstance(language, str):
        raise ValidationError("Language is required")

    if language != "python":
        raise ValidationError("GPU Sandboxes currently only support Python. Additional languages coming soon.")


def _get_file_extension(language: str) -> str:
    extensions: dict[str, str] = {
        "javascript": ".js",
        "typescript": ".ts",
        "python": ".py",
        "go": ".go",
        "shell": ".sh",
    }
    return extensions.get(language, ".py")


def _get_default_runtime(language: str) -> str:
    if language == "javascript":
        raise ValidationError('JavaScript requires explicit runtime: "nodejs" or "deno"')
    return language


def _is_local_path(path: str) -> bool:
    if not path:
        return False
    return (path.startswith("/") or path.startswith("./") or path.startswith("../")) and Path(path).exists()


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


def _format_requirements(requirements: str | list[str] | None) -> str:
    if not requirements:
        return ""
    if isinstance(requirements, list):
        return "\n".join(requirements)
    return requirements


def _get_local_model_info(model_path: str, sandbox_name: str) -> dict[str, Any]:
    """Collect local model file metadata."""
    path = Path(model_path)
    if not path.is_dir():
        raise ValidationError("Model path must be a directory")

    local_upload_file_name = path.name
    sanitized_model_name = _sanitize_model_name(sandbox_name)
    files = get_files_in_directory(model_path)

    if not files:
        raise ValidationError("No files found in model directory")

    files_within_model_folder = [
        {
            "name": f["name"],
            "size": f["size"],
            "type": f["type"],
            "webkitRelativePath": f["webkit_relative_path"],
        }
        for f in files
    ]

    file_names_within_model_folder = [f["name"] for f in files]

    return {
        "files": files,
        "files_within_model_folder": files_within_model_folder,
        "file_names_within_model_folder": file_names_within_model_folder,
        "local_upload_file_name": local_upload_file_name,
        "sanitized_model_name": sanitized_model_name,
    }


def _build_request_body(config: GPUSandboxConfig, local_model_info: dict[str, Any] | None) -> dict[str, Any]:
    name = config["name"].lower()
    language = config["language"]
    runtime = config.get("runtime") or _get_default_runtime(language)
    code = config.get("code", "")
    file_ext = _get_file_extension(language)
    gpu = config.get("gpu", "T4")
    requirements = _format_requirements(config.get("requirements"))

    has_local_model = local_model_info is not None
    model_name = local_model_info["sanitized_model_name"] if has_local_model else None

    cpu_cores = config.get("vcpus") or 10

    body: dict[str, Any] = {
        "name": name,
        "language": language,
        "runtime": runtime,
        "sourceWith": code,
        "sourceWithout": code,
        "fileExt": file_ext,
        "processorType": "GPU",
        "sandboxType": "gpu",
        "gpu": gpu,
        "memoryAllocated": parse_memory(config["memory"]) if config.get("memory") else 10000,
        "timeout": config.get("timeout", 300),
        "cpuCores": cpu_cores,  # vCPUs for the GPU sandbox VM (hotplugged at runtime)
        "envVariables": json.dumps(config.get("env_variables", [])),
        "requirements": requirements,
        "cronExpression": "",
        "totalVariables": len(config.get("env_variables", [])),
        "selectedFramework": detect_framework(requirements),
        "useEmptyFolder": not has_local_model,
        "modelPath": (
            f"{local_model_info['sanitized_model_name']}/mnt/storage/{local_model_info['local_upload_file_name']}"
            if has_local_model
            else None
        ),
        "selectedFunction": {
            "name": name,
            "sourceWith": code,
            "runtime": runtime,
            "language": language,
            "sizeInBytes": len(code.encode("utf-8")) if code else 0,
        },
        "selectedModel": (
            {
                "name": local_model_info["sanitized_model_name"],
                "modelName": local_model_info["sanitized_model_name"],
                "currentModelName": local_model_info["local_upload_file_name"],
                "isCreatingNewModel": True,
                "gpufProjectTitleState": local_model_info["sanitized_model_name"],
                "useEmptyFolder": False,
                "files": local_model_info["files_within_model_folder"],
            }
            if has_local_model
            else {
                "currentModelName": None,
                "isCreatingNewModel": True,
                "gpufProjectTitleState": "test",
                "useEmptyFolder": True,
            }
        ),
        "filesWithinModelFolder": local_model_info["files_within_model_folder"] if has_local_model else [],
        "fileNamesWithinModelFolder": local_model_info["file_names_within_model_folder"] if has_local_model else [],
        "modelName": model_name,
    }

    return body


def _create_gpu_sandbox_instance(
    sandbox_id: str,
    name: str,
    runtime: str,
    gpu: GPUType,
    endpoint: str,
    api_token: str,
    gpu_build_url: str,
    base_url: str,
) -> DotDict:
    """Create a GPU sandbox instance with run/upload/delete methods."""
    deleted = {"value": False}

    async def run(code: str | None = None) -> RunResult:
        if deleted["value"]:
            raise BuildfunctionsError("Sandbox has been deleted", "INVALID_REQUEST")

        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
            response = await client.post(
                endpoint,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_token}",
                },
            )

        response_text = response.text
        if not response_text:
            raise BuildfunctionsError("Empty response from sandbox", "UNKNOWN_ERROR", response.status_code)

        if not response.is_success:
            raise BuildfunctionsError(f"Execution failed: {response_text}", "UNKNOWN_ERROR", response.status_code)

        # Try to parse as JSON, otherwise return raw text
        try:
            data = json.loads(response_text)
        except json.JSONDecodeError:
            data = response_text

        return RunResult(
            response=data,
            status=response.status_code,
        )

    async def upload(options: UploadOptions) -> None:
        if deleted["value"]:
            raise BuildfunctionsError("Sandbox has been deleted", "INVALID_REQUEST")

        local_path = options.get("local_path")
        file_path = options.get("file_path")

        if not local_path or not file_path:
            raise ValidationError("Both local_path and file_path are required")

        local = Path(local_path)
        if not local.exists():
            raise ValidationError(f"Local file not found: {local_path}")

        content = local.read_text(encoding="utf-8")

        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
            response = await client.post(
                f"{base_url}/api/sdk/sandbox/upload",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_token}",
                },
                json={
                    "sandboxId": sandbox_id,
                    "filePath": file_path,
                    "content": content,
                    "type": "gpu",
                },
            )

        if not response.is_success:
            raise BuildfunctionsError("Upload failed", "UNKNOWN_ERROR", response.status_code)

    async def delete_fn() -> None:
        if deleted["value"]:
            return

        # Use the same endpoint as CPU sandbox - buildfunctions web app handles the delete
        # This ensures proper HOST cleanup for occupied VMs
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            response = await client.request(
                "DELETE",
                f"{base_url}/api/sdk/sandbox/delete",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_token}",
                },
                json={
                    "sandboxId": sandbox_id,
                    "type": "gpu",
                },
            )

        if not response.is_success:
            raise BuildfunctionsError("Delete failed", "UNKNOWN_ERROR", response.status_code)

        deleted["value"] = True

    return DotDict({
        "id": sandbox_id,
        "name": name,
        "runtime": runtime,
        "endpoint": endpoint,
        "type": "gpu",
        "gpu": gpu,
        "run": run,
        "upload": upload,
        "delete": delete_fn,
    })


async def _create_gpu_sandbox(config: GPUSandboxConfig) -> DotDict:
    """Create a new GPU sandbox."""
    if not _global_api_token:
        raise ValidationError("API key not set. Initialize Buildfunctions client first.")

    _validate_config(config)

    gpu_build_url = _global_gpu_build_url or DEFAULT_GPU_BUILD_URL
    base_url = _global_base_url or DEFAULT_BASE_URL
    api_token = _global_api_token

    # Check if model is a local path
    model_config = config.get("model")
    model_path = model_config if isinstance(model_config, str) else (model_config.get("path") if isinstance(model_config, dict) else None)
    local_model_info: dict[str, Any] | None = None

    if model_path and _is_local_path(model_path):
        print(f"   Local model detected: {model_path}")
        local_model_info = _get_local_model_info(model_path, config["name"])
        print(f"   Found {len(local_model_info['files'])} files to upload")

    # Resolve code (inline string or file path)
    resolved_code = await resolve_code(config["code"]) if config.get("code") else ""
    resolved_config = {**config, "code": resolved_code}

    request_body = _build_request_body(resolved_config, local_model_info)

    body = {
        **request_body,
        "userId": _global_user_id,
        "username": _global_username,
        "computeTier": _global_compute_tier,
        "runCommand": None,
    }

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(1800.0)) as client:
            response = await client.post(
                f"{gpu_build_url}/build",
                headers={
                    "Content-Type": "application/json",
                    "Connection": "keep-alive",
                },
                json=body,
            )
    except httpx.TimeoutException:
        raise BuildfunctionsError("GPU sandbox build timed out", "NETWORK_ERROR")

    if response.status_code not in (200, 201):
        raise BuildfunctionsError(
            f"Failed to create sandbox: {response.text}", "UNKNOWN_ERROR", response.status_code
        )

    try:
        data = response.json()
    except Exception:
        data = {"success": response.status_code == 201}

    # Upload local model files if present
    if local_model_info:
        model_presigned = (data.get("modelAndFunctionPresignedUrls") or {}).get("modelPresignedUrls")
        if model_presigned:
            print("   Uploading model files to S3...")
            try:
                await upload_model_files(
                    local_model_info["files"],
                    model_presigned,
                    data.get("bucketName", ""),
                    base_url,
                )
                print("   Model files uploaded successfully")
            except Exception as e:
                raise BuildfunctionsError(
                    f"Sandbox created but model upload failed: {e}", "UNKNOWN_ERROR"
                )

    sandbox_id = (data.get("data") or {}).get("siteId") or data.get("siteId") or data.get("id")
    name = config["name"].lower()
    sandbox_runtime = config.get("runtime", config["language"])
    sandbox_endpoint = (
        data.get("endpoint")
        or (data.get("data") or {}).get("sslCertificateEndpoint")
        or f"https://{name}.buildfunctions.app"
    )

    return _create_gpu_sandbox_instance(
        sandbox_id or name,
        name,
        sandbox_runtime,
        config.get("gpu", "T4"),
        sandbox_endpoint,
        api_token,
        gpu_build_url,
        base_url,
    )


class GPUSandbox:
    """GPU Sandbox factory - matches TypeScript SDK pattern."""

    @staticmethod
    async def create(config: GPUSandboxConfig) -> DotDict:
        """Create a new GPU sandbox."""
        return await _create_gpu_sandbox(config)


# Alias for direct function call style
create_gpu_sandbox = _create_gpu_sandbox
