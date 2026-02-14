"""GPU Function - Deploy GPU-accelerated serverless functions to Buildfunctions."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import httpx

from buildfunctions.dotdict import DotDict
from buildfunctions.errors import ValidationError
from buildfunctions.framework import detect_framework
from buildfunctions.memory import parse_memory
from buildfunctions.resolve_code import resolve_code
from buildfunctions.types import DeployedFunction, GPUFunctionOptions

DEFAULT_GPU_BUILD_URL = "https://prod-gpu-build.buildfunctions.link"
DEFAULT_BASE_URL = "https://www.buildfunctions.com"

# Module-level state
_global_api_token: str | None = None
_global_gpu_build_url: str | None = None
_global_base_url: str | None = None
_global_user_id: str | None = None
_global_username: str | None = None
_global_compute_tier: str | None = None


def set_gpu_api_token(
    api_token: str,
    gpu_build_url: str | None = None,
    base_url: str | None = None,
    user_id: str | None = None,
    username: str | None = None,
    compute_tier: str | None = None,
) -> None:
    """Set the API token for GPU function deployment."""
    global _global_api_token, _global_gpu_build_url, _global_base_url, _global_user_id, _global_username, _global_compute_tier
    _global_api_token = api_token
    _global_gpu_build_url = gpu_build_url
    _global_base_url = base_url
    _global_user_id = user_id
    _global_username = username
    _global_compute_tier = compute_tier


def _get_file_extension(language: str) -> str:
    extensions: dict[str, str] = {
        "javascript": ".js",
        "typescript": ".ts",
        "python": ".py",
        "go": ".go",
        "shell": ".sh",
    }
    return extensions.get(language, ".js")


def _get_default_runtime(language: str) -> str:
    if language == "javascript":
        raise ValidationError('JavaScript requires explicit runtime: "nodejs" or "deno"')
    return language


def _format_requirements(requirements: str | list[str] | None) -> str:
    if not requirements:
        return ""
    if isinstance(requirements, list):
        return "\n".join(requirements)
    return requirements


def _validate_options(options: GPUFunctionOptions) -> None:
    name = options.get("name")
    if not name or not isinstance(name, str):
        raise ValidationError("Function name is required")

    import re

    if not re.match(r"^[a-z0-9-]+$", name.lower()):
        raise ValidationError("Function name can only contain lowercase letters, numbers, and hyphens")

    code = options.get("code")
    if not code or not isinstance(code, str):
        raise ValidationError("Function code is required")

    if not options.get("language"):
        raise ValidationError("Language is required")

    if options.get("language") != "python":
        raise ValidationError("GPU Functions currently only support Python. Additional languages coming soon.")

    gpu_count = options.get("gpu_count")
    if gpu_count is not None:
        if not isinstance(gpu_count, int) or gpu_count < 1 or gpu_count > 10:
            raise ValidationError("gpu_count must be an integer between 1 and 10")


def _build_request_body(options: GPUFunctionOptions) -> dict[str, Any]:
    name = options["name"]
    language = options["language"]
    code = options["code"]
    config = options.get("config") or {}
    env_variables = options.get("env_variables", {})
    cron_schedule = options.get("cron_schedule")
    framework = options.get("framework")

    runtime = options.get("runtime") or _get_default_runtime(language)
    gpu = "T4G" if options.get("gpu", "T4G") == "T4" else options.get("gpu", "T4G")
    file_ext = _get_file_extension(language)
    function_name = name.lower()

    # Support top-level memory/timeout (preferred) or nested config.memory/config.timeout
    memory_raw = options.get("memory") or config.get("memory")
    timeout_raw = options.get("timeout") or config.get("timeout")
    # Support top-level requirements (preferred) or dependencies
    requirements_raw = options.get("requirements") or options.get("dependencies")
    requirements = _format_requirements(requirements_raw)

    env_vars_list = [{"key": k, "value": v} for k, v in env_variables.items()] if env_variables else []

    # When gpu_count >= 2, user specifies totals — divide per VM
    gpu_count = options.get("gpu_count") or 1
    per_vm_divisor = gpu_count if gpu_count >= 2 else 1
    memory_total = parse_memory(memory_raw) if memory_raw else 4096
    vcpus_total = options.get("vcpus") or 10

    return {
        "name": function_name,
        "language": language,
        "runtime": runtime,
        "sourceWith": code,
        "sourceWithout": code,
        "fileExt": file_ext,
        "processorType": "GPU",
        "gpu": gpu,
        "memoryAllocated": memory_total // per_vm_divisor,
        "timeout": timeout_raw or 180,
        "cpuCores": vcpus_total // per_vm_divisor,
        "envVariables": json.dumps(env_vars_list),
        "requirements": requirements,
        "cronExpression": cron_schedule or "",
        "totalVariables": len(env_variables) if env_variables else 0,
        "selectedFramework": framework or detect_framework(requirements),
        "useEmptyFolder": True,
        "selectedFunction": {
            "name": function_name,
            "sourceWith": code,
            "runtime": runtime,
            "language": language,
            "sizeInBytes": len(code.encode("utf-8")),
        },
        "selectedModel": {
            "currentModelName": None,
            "isCreatingNewModel": True,
            "gpufProjectTitleState": "test",
            "useEmptyFolder": True,
        },
        "gpuCount": gpu_count,
    }


async def _create_gpu_function(options: GPUFunctionOptions) -> DeployedFunction | None:
    """Internal function to create and deploy a GPU function."""
    if not _global_api_token:
        raise ValidationError("API key not set. Initialize Buildfunctions client first.")

    api_token = _global_api_token
    gpu_build_url = _global_gpu_build_url or DEFAULT_GPU_BUILD_URL
    base_url = _global_base_url or DEFAULT_BASE_URL
    user_id = _global_user_id
    username = _global_username
    compute_tier = _global_compute_tier

    resolved_code = await resolve_code(options["code"])
    resolved_options = {**options, "code": resolved_code}
    _validate_options(resolved_options)

    resolved_runtime = resolved_options.get("runtime") or _get_default_runtime(resolved_options["language"])

    body = {
        **_build_request_body(resolved_options),
        "userId": user_id,
        "username": username,
        "computeTier": compute_tier,
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
        return None

    if response.status_code not in (200, 201):
        return None

    try:
        data = response.json()
    except Exception:
        data = {"success": response.status_code == 201}

    site_id = (data.get("data") or {}).get("siteId") or data.get("siteId") or data.get("id")
    func_name = options["name"].lower()
    endpoint = data.get("endpoint") or f"https://{func_name}.buildfunctions.app"

    config = options.get("config", {})
    now = datetime.now(timezone.utc).isoformat()

    async def delete_fn() -> None:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            await client.request(
                "DELETE",
                f"{base_url}/api/sdk/function/delete",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_token}",
                },
                json={"siteId": site_id},
            )

    return DotDict({
        "id": site_id or "",
        "name": func_name,
        "subdomain": func_name,
        "endpoint": endpoint,
        "url": (data.get("data") or {}).get("sslCertificateEndpoint", ""),
        "language": options["language"],
        "runtime": resolved_runtime,
        "memoryAllocated": parse_memory(options.get("memory") or config.get("memory") or 4096) if (options.get("memory") or config.get("memory")) else 4096,
        "timeoutSeconds": options.get("timeout") or config.get("timeout") or 180,
        "isGPUF": True,
        "framework": options.get("framework", ""),
        "createdAt": now,
        "updatedAt": now,
        "delete": delete_fn,
    })


class GPUFunction:
    """GPU Function factory - matches TypeScript SDK pattern."""

    @staticmethod
    async def create(options: GPUFunctionOptions) -> DeployedFunction | None:
        """Create and deploy a new GPU function."""
        return await _create_gpu_function(options)


create_gpu_function = _create_gpu_function
