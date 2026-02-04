"""Buildfunctions SDK Client."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from buildfunctions.cpu_function import set_api_token
from buildfunctions.cpu_sandbox import set_cpu_sandbox_api_token
from buildfunctions.dotdict import DotDict
from buildfunctions.errors import NotFoundError
from buildfunctions.framework import detect_framework
from buildfunctions.gpu_function import GPUFunction, set_gpu_api_token
from buildfunctions.gpu_sandbox import set_gpu_sandbox_api_token
from buildfunctions.http_client import create_http_client
from buildfunctions.memory import parse_memory
from buildfunctions.resolve_code import get_caller_file, resolve_code
from buildfunctions.types import (
    AuthResponse,
    BuildfunctionsConfig,
    CreateFunctionOptions,
    DeployedFunction,
    FindUniqueOptions,
    ListOptions,
)

DEFAULT_BASE_URL = "https://www.buildfunctions.com"
DEFAULT_GPU_BUILD_URL = "https://prod-gpu-build.buildfunctions.link"


def _format_requirements(requirements: str | list[str] | None) -> str:
    if not requirements:
        return ""
    if isinstance(requirements, list):
        return "\n".join(requirements)
    return requirements


def _get_default_runtime(language: str) -> str:
    if language == "javascript":
        raise ValueError('JavaScript requires explicit runtime: "nodejs" or "deno"')
    return language


def _get_file_extension(language: str) -> str:
    match language:
        case "javascript":
            return ".js"
        case "typescript":
            return ".ts"
        case "python":
            return ".py"
        case "go":
            return ".go"
        case "shell":
            return ".sh"
        case _:
            return ".js"


def _create_functions_manager(http: dict[str, Any]) -> DotDict:
    """Create a functions manager with list/findUnique/get/create/delete methods."""

    def _wrap_function(fn: dict[str, Any]) -> DotDict:
        async def delete_fn() -> None:
            await http["delete"]("/api/sdk/functions/build", {"siteId": fn["id"]})

        return DotDict({**fn, "delete": delete_fn})

    async def list_fn(options: ListOptions | None = None) -> list[DotDict]:
        page = (options or {}).get("page", 1)
        response = await http["get"]("/api/sdk/functions", {"page": page})
        return [_wrap_function(fn) for fn in response["stringifiedQueryResults"]]

    async def find_unique(options: FindUniqueOptions) -> DotDict | None:
        where = options.get("where", options) if isinstance(options, dict) else options

        if where.get("id"):
            try:
                fn = await http["get"]("/api/sdk/functions/build", {"siteId": where["id"]})
                return _wrap_function(fn)
            except NotFoundError:
                return None

        if where.get("name"):
            functions = await list_fn()
            for fn in functions:
                if fn.get("name") == where["name"]:
                    return fn
            return None

        return None

    async def get(site_id: str) -> DotDict:
        fn = await http["get"]("/api/sdk/functions/build", {"siteId": site_id})
        return _wrap_function(fn)

    async def create(options: CreateFunctionOptions) -> DotDict:
        # Get the caller's file location to resolve relative paths correctly
        caller_file = get_caller_file()
        caller_dir = caller_file.parent if caller_file else None

        # Resolve code (inline string or file path) relative to the caller's location
        resolved_code = await resolve_code(options["code"], caller_dir)

        file_ext = _get_file_extension(options["language"])
        name = options["name"].lower()
        is_gpu = options.get("processor_type") == "GPU" or bool(options.get("gpu"))
        runtime = options.get("runtime") or _get_default_runtime(options["language"])

        if is_gpu:
            requirements = _format_requirements(options.get("requirements"))
            env_variables_list = options.get("env_variables", [])
            env_dict = {v["key"]: v["value"] for v in env_variables_list} if env_variables_list else {}

            deployed = await GPUFunction.create({
                "name": options["name"],
                "code": resolved_code,
                "language": options["language"],
                "runtime": runtime,
                "gpu": options.get("gpu", "T4"),
                "vcpus": options.get("vcpus"),
                "config": {
                    "memory": parse_memory(options["memory"]) if options.get("memory") else 1024,
                    "timeout": options.get("timeout", 60),
                },
                "dependencies": requirements,
                "env_variables": env_dict if env_dict else {},
                "cron_schedule": options.get("cron_schedule", ""),
                "framework": options.get("framework") or detect_framework(requirements),
                "model_name": options.get("model_name", ""),
                "model_path": options.get("model_path", ""),
            })

            if not deployed:
                raise RuntimeError("GPU Function deployment failed")
            return DotDict(deployed) if not isinstance(deployed, DotDict) else deployed

        # CPU build
        body = {
            "name": name,
            "fileExt": file_ext,
            "sourceWith": resolved_code,
            "sourceWithout": resolved_code,
            "language": options["language"],
            "runtime": runtime,
            "memoryAllocated": parse_memory(options["memory"]) if options.get("memory") else 128,
            "timeout": options.get("timeout", 10),
            "envVariables": json.dumps(options.get("env_variables", [])),
            "requirements": _format_requirements(options.get("requirements")),
            "cronExpression": options.get("cron_schedule", ""),
            "processorType": "CPU",
            "selectedFramework": options.get("framework") or detect_framework(
                _format_requirements(options.get("requirements"))
            ),
            "subdomain": name,
            "totalVariables": len(options.get("env_variables", [])),
            "functionCount": 0,
        }

        response = await http["post"]("/api/sdk/functions/build", body)
        now = datetime.now(timezone.utc).isoformat()

        return _wrap_function({
            "id": response["siteId"],
            "name": name,
            "subdomain": name,
            "endpoint": response["endpoint"],
            "lambdaUrl": response.get("sslCertificateEndpoint", ""),
            "language": options["language"],
            "runtime": runtime,
            "lambdaMemoryAllocated": parse_memory(options["memory"]) if options.get("memory") else 128,
            "timeoutSeconds": options.get("timeout", 10),
            "isGPUF": False,
            "framework": options.get("framework", ""),
            "createdAt": now,
            "updatedAt": now,
        })

    async def delete_fn(site_id: str) -> None:
        await http["delete"]("/api/sdk/functions/build", {"siteId": site_id})

    return DotDict({
        "list": list_fn,
        "findUnique": find_unique,
        "find_unique": find_unique,
        "get": get,
        "create": create,
        "delete": delete_fn,
    })


async def Buildfunctions(config: BuildfunctionsConfig | None = None) -> DotDict:
    """Create a Buildfunctions SDK client.

    Authenticates with the API and returns a client with:
    - functions: Functions manager (list, findUnique, get, create, delete)
    - user: Authenticated user info
    - sessionExpiresAt: Session expiration timestamp
    - authenticatedAt: Authentication timestamp
    - getHttpClient: Returns the underlying HTTP client

    Supports both dot notation and bracket notation:
        client.user.username  OR  client["user"]["username"]
    """
    if config is None:
        import os

        from dotenv import load_dotenv

        load_dotenv()
        api_token = os.environ.get("BUILDFUNCTIONS_API_TOKEN", "")
        config = BuildfunctionsConfig(api_token=api_token)

    api_token = config.get("api_token") or config.get("apiToken", "")
    if not api_token:
        raise ValueError("API token is required")

    base_url = config.get("base_url") or config.get("baseUrl", DEFAULT_BASE_URL)
    gpu_build_url = config.get("gpu_build_url") or config.get("gpuBuildUrl", DEFAULT_GPU_BUILD_URL)

    # Don't wrap http in DotDict - it has methods like 'get' that conflict with dict builtins
    http = create_http_client(base_url=base_url, api_token=api_token)

    auth_response: AuthResponse = await http["post"]("/api/sdk/auth")

    if not auth_response.get("authenticated"):
        raise RuntimeError("Authentication failed")

    http["set_token"](auth_response["sessionToken"])

    user_id = auth_response["user"].get("id", "")
    username = auth_response["user"].get("username") or None
    compute_tier = auth_response["user"].get("compute_tier") or auth_response["user"].get("computeTier") or None

    set_cpu_sandbox_api_token(auth_response["sessionToken"], base_url)
    set_gpu_sandbox_api_token(auth_response["sessionToken"], gpu_build_url, user_id, username, compute_tier, base_url)
    set_gpu_api_token(auth_response["sessionToken"], gpu_build_url, base_url, user_id, username, compute_tier)
    set_api_token(auth_response["sessionToken"], base_url)

    functions = _create_functions_manager(http)

    return DotDict({
        "functions": functions,
        "user": DotDict(auth_response["user"]),
        # Support both naming conventions
        "sessionExpiresAt": auth_response.get("expiresAt"),
        "session_expires_at": auth_response.get("expiresAt"),
        "authenticatedAt": auth_response.get("authenticatedAt"),
        "authenticated_at": auth_response.get("authenticatedAt"),
        "getHttpClient": lambda: http,
        "get_http_client": lambda: http,
    })


async def createClient(config: BuildfunctionsConfig | None = None) -> dict[str, Any] | None:
    """Create a Buildfunctions client, returning None on failure."""
    try:
        return await Buildfunctions(config)
    except Exception:
        return None


# Aliases for snake_case compatibility
buildfunctions = Buildfunctions
create_client = createClient


def init(
    api_token: str,
    base_url: str | None = None,
    gpu_build_url: str | None = None,
    user_id: str | None = None,
    username: str | None = None,
    compute_tier: str | None = None,
) -> None:
    """Global initialization - sets tokens across all modules."""
    set_api_token(api_token, base_url)
    set_gpu_api_token(api_token, gpu_build_url, base_url, user_id, username, compute_tier)
    set_cpu_sandbox_api_token(api_token, base_url)
    set_gpu_sandbox_api_token(api_token, gpu_build_url, user_id, username, compute_tier, base_url)
