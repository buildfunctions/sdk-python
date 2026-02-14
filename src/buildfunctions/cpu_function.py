"""CPU Function - Deploy serverless functions to Buildfunctions."""

from __future__ import annotations

import json
from typing import Any

import httpx

from buildfunctions.dotdict import DotDict
from buildfunctions.errors import ValidationError
from buildfunctions.memory import parse_memory
from buildfunctions.resolve_code import resolve_code
from buildfunctions.types import CPUFunctionOptions, DeployedFunction

DEFAULT_BASE_URL = "https://www.buildfunctions.com"

# Module-level state
_global_api_token: str | None = None
_global_base_url: str | None = None


def set_api_token(api_token: str, base_url: str | None = None) -> None:
    """Set the API token for function deployment."""
    global _global_api_token, _global_base_url
    _global_api_token = api_token
    _global_base_url = base_url


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


def _validate_options(options: CPUFunctionOptions) -> None:
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


def _build_request_body(options: CPUFunctionOptions) -> dict[str, Any]:
    name = options["name"]
    language = options["language"]
    code = options["code"]
    config = options.get("config", {})
    env_variables = options.get("env_variables", {})
    dependencies = options.get("dependencies")
    cron_schedule = options.get("cron_schedule")

    runtime = options.get("runtime") or _get_default_runtime(language)
    file_ext = _get_file_extension(language)

    env_vars_list = [{"key": k, "value": v} for k, v in env_variables.items()] if env_variables else []

    return {
        "name": name.lower(),
        "language": language,
        "runtime": runtime,
        "sourceWith": code,
        "fileExt": file_ext,
        "processorType": "CPU only",
        "memoryAllocated": parse_memory(config.get("memory", 1024)) if config.get("memory") else 1024,
        "timeout": config.get("timeout", 10) if config else 10,
        "envVariables": json.dumps(env_vars_list),
        "requirements": _format_requirements(dependencies),
        "cronExpression": cron_schedule or "",
        "totalVariables": len(env_variables) if env_variables else 0,
    }


async def _create_cpu_function(options: CPUFunctionOptions) -> DeployedFunction | None:
    """Internal function to create and deploy a CPU function."""
    if not _global_api_token:
        raise ValidationError("API key not set. Initialize Buildfunctions client first.")

    api_token = _global_api_token
    base_url = _global_base_url or DEFAULT_BASE_URL

    resolved_code = await resolve_code(options["code"])
    resolved_options = {**options, "code": resolved_code}
    _validate_options(resolved_options)
    body = _build_request_body(resolved_options)

    async with httpx.AsyncClient(timeout=httpx.Timeout(600.0)) as client:
        response = await client.post(
            f"{base_url}/api/sdk/function/build",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_token}",
            },
            json=body,
        )

    if not response.is_success:
        return None

    data = response.json()
    name = options["name"].lower()
    runtime = options.get("runtime") or _get_default_runtime(options["language"])

    async def delete_fn() -> None:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as c:
            await c.request(
                "DELETE",
                f"{base_url}/api/sdk/function/build",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_token}",
                },
                json={"siteId": data.get("siteId")},
            )

    return DotDict({
        "id": data.get("siteId", ""),
        "name": name,
        "subdomain": name,
        "endpoint": data.get("endpoint", ""),
        "url": data.get("sslCertificateEndpoint", ""),
        "language": options["language"],
        "runtime": runtime,
        "isGPUF": False,
        "delete": delete_fn,
    })


class CPUFunction:
    """CPU Function factory - matches TypeScript SDK pattern."""

    @staticmethod
    async def create(options: CPUFunctionOptions) -> DeployedFunction | None:
        """Create and deploy a new CPU function."""
        return await _create_cpu_function(options)


create_cpu_function = _create_cpu_function
