"""CPU Sandbox - Hardware-isolated execution environment for untrusted AI actions."""

from __future__ import annotations

import asyncio
import json
import socket
import struct
from pathlib import Path
from typing import Any

import httpx

from buildfunctions.dotdict import DotDict
from buildfunctions.errors import BuildfunctionsError, ValidationError
from buildfunctions.memory import parse_memory
from buildfunctions.resolve_code import resolve_code
from buildfunctions.types import CPUSandboxConfig, CPUSandboxInstance, RunResult, UploadOptions

DEFAULT_BASE_URL = "https://www.buildfunctions.com"

# AWS Route53 authoritative nameservers for buildfunctions.app
AWS_NAMESERVERS = [
    "205.251.193.143",
    "205.251.198.254",
    "205.251.195.249",
    "205.251.198.95",
]

# Module-level state
_global_api_token: str | None = None
_global_base_url: str | None = None


def set_cpu_sandbox_api_token(api_token: str, base_url: str | None = None) -> None:
    """Set the API token for sandbox operations."""
    global _global_api_token, _global_base_url
    _global_api_token = api_token
    _global_base_url = base_url


def _format_requirements(requirements: str | list[str] | None) -> str:
    if not requirements:
        return ""
    if isinstance(requirements, list):
        return "\n".join(requirements)
    return requirements


def _validate_config(config: CPUSandboxConfig) -> None:
    name = config.get("name")
    if not name or not isinstance(name, str):
        raise ValidationError("Sandbox name is required")

    language = config.get("language")
    if not language or not isinstance(language, str):
        raise ValidationError("Language is required")

    if language == "javascript" and not config.get("runtime"):
        raise ValidationError('JavaScript requires explicit runtime: "node" or "deno"')


def _build_dns_query(hostname: str) -> bytes:
    """Build a DNS A record query packet."""
    import random
    transaction_id = random.randint(0, 65535)
    flags = 0x0100  # Standard query with recursion desired
    questions = 1
    answer_rrs = 0
    authority_rrs = 0
    additional_rrs = 0

    header = struct.pack(">HHHHHH", transaction_id, flags, questions, answer_rrs, authority_rrs, additional_rrs)

    # Build question section
    question = b""
    for part in hostname.split("."):
        question += bytes([len(part)]) + part.encode("ascii")
    question += b"\x00"  # End of name
    question += struct.pack(">HH", 1, 1)  # Type A, Class IN

    return header + question


def _parse_dns_response(response: bytes) -> str | None:
    """Parse DNS response and extract first A record IP."""
    if len(response) < 12:
        return None

    # Skip header (12 bytes) and question section
    pos = 12

    # Skip question name
    while pos < len(response) and response[pos] != 0:
        if response[pos] & 0xC0 == 0xC0:  # Compression pointer
            pos += 2
            break
        pos += response[pos] + 1
    else:
        pos += 1  # Skip null terminator

    pos += 4  # Skip QTYPE and QCLASS

    # Parse answer section
    while pos < len(response):
        # Skip name (may be compressed)
        if response[pos] & 0xC0 == 0xC0:
            pos += 2
        else:
            while pos < len(response) and response[pos] != 0:
                pos += response[pos] + 1
            pos += 1

        if pos + 10 > len(response):
            break

        rtype, rclass, ttl, rdlength = struct.unpack(">HHIH", response[pos:pos + 10])
        pos += 10

        if rtype == 1 and rdlength == 4:  # A record
            ip_bytes = response[pos:pos + 4]
            return ".".join(str(b) for b in ip_bytes)

        pos += rdlength

    return None


def _resolve_with_aws(hostname: str) -> str | None:
    """Resolve hostname using AWS Route53 authoritative nameservers via raw UDP DNS."""
    query = _build_dns_query(hostname)

    for nameserver in AWS_NAMESERVERS:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(2.0)
            sock.sendto(query, (nameserver, 53))
            response, _ = sock.recvfrom(512)
            sock.close()

            ip = _parse_dns_response(response)
            if ip:
                return ip
        except Exception:
            continue

    return None


async def _https_get_with_ip(ip: str, hostname: str, path: str) -> dict[str, Any]:
    """HTTPS GET using resolved IP (bypasses system DNS)."""
    transport = httpx.AsyncHTTPTransport(
        verify=True,
    )
    async with httpx.AsyncClient(
        transport=transport,
        timeout=httpx.Timeout(10.0),
    ) as client:
        response = await client.get(
            f"https://{ip}{path}",
            headers={"Host": hostname},
            extensions={"sni_hostname": hostname},
        )
        return {"status": response.status_code, "body": response.text}


async def _wait_for_endpoint(endpoint: str, max_attempts: int = 60, delay_ms: int = 500) -> None:
    """Wait for endpoint using AWS Route53 authoritative DNS."""
    from urllib.parse import urlparse

    parsed = urlparse(endpoint)
    hostname = parsed.hostname or ""
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    for attempt in range(1, max_attempts + 1):
        try:
            ip = _resolve_with_aws(hostname)
            if not ip:
                raise RuntimeError("DNS resolution failed")

            result = await _https_get_with_ip(ip, hostname, path)
            if 200 <= result["status"] < 500:
                return
        except Exception as e:
            if attempt == 1 or attempt % 10 == 0:
                print(f"   Waiting... (attempt {attempt}/{max_attempts})")

        await asyncio.sleep(delay_ms / 1000.0)

    raise BuildfunctionsError(f"Endpoint not ready after {max_attempts} attempts", "NETWORK_ERROR")


async def _fetch_with_auth_dns(endpoint: str) -> dict[str, Any]:
    """Fetch endpoint using AWS Route53 authoritative DNS."""
    from urllib.parse import urlparse

    parsed = urlparse(endpoint)
    hostname = parsed.hostname or ""
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    ip = _resolve_with_aws(hostname)
    if not ip:
        raise BuildfunctionsError("DNS resolution failed", "NETWORK_ERROR")

    return await _https_get_with_ip(ip, hostname, path)


def _create_cpu_sandbox_instance(
    sandbox_id: str,
    name: str,
    runtime: str,
    endpoint: str,
    api_token: str,
    base_url: str,
) -> DotDict:
    """Create a CPU sandbox instance with run/upload/delete methods."""
    deleted = {"value": False}

    async def run(code: str | None = None) -> RunResult:
        if deleted["value"]:
            raise BuildfunctionsError("Sandbox has been deleted", "INVALID_REQUEST")

        await _wait_for_endpoint(endpoint)

        response = await _fetch_with_auth_dns(endpoint)
        response_text = response["body"]

        if not response_text:
            raise BuildfunctionsError("Empty response from sandbox", "UNKNOWN_ERROR", response["status"])

        if response["status"] < 200 or response["status"] >= 300:
            raise BuildfunctionsError(f"Execution failed: {response_text}", "UNKNOWN_ERROR", response["status"])

        # Try to parse as JSON, otherwise return raw text
        try:
            data = json.loads(response_text)
        except json.JSONDecodeError:
            data = response_text

        return RunResult(
            response=data,
            status=response["status"],
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
                    "type": "cpu",
                },
            )

        if not response.is_success:
            raise BuildfunctionsError("Upload failed", "UNKNOWN_ERROR", response.status_code)

    async def delete_fn() -> None:
        if deleted["value"]:
            return

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
                    "type": "cpu",
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
        "type": "cpu",
        "run": run,
        "upload": upload,
        "delete": delete_fn,
    })


async def _create_cpu_sandbox(config: CPUSandboxConfig) -> DotDict:
    """Create a new CPU sandbox."""
    if not _global_api_token:
        raise ValidationError("API key not set. Initialize Buildfunctions client first.")

    _validate_config(config)

    base_url = _global_base_url or DEFAULT_BASE_URL
    api_token = _global_api_token

    name = config["name"].lower()
    language = config["language"]
    file_ext = ".py" if language == "python" else ".js" if language == "javascript" else ".py"

    # Resolve code (inline string or file path)
    resolved_code = await resolve_code(config["code"]) if config.get("code") else ""

    request_body = {
        "type": "cpu",
        "name": name,
        "fileExt": file_ext,
        "code": resolved_code,
        "sourceWith": resolved_code,
        "sourceWithout": resolved_code,
        "language": language,
        "runtime": config.get("runtime", language),
        "memoryAllocated": parse_memory(config["memory"]) if config.get("memory") else 128,
        "timeout": config.get("timeout", 10),
        "envVariables": json.dumps(config.get("env_variables", [])),
        "requirements": _format_requirements(config.get("requirements")),
        "cronExpression": "",
        "subdomain": name,
        "totalVariables": len(config.get("env_variables", [])),
        "functionCount": 0,
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
        response = await client.post(
            f"{base_url}/api/sdk/sandbox/create",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_token}",
            },
            json=request_body,
        )

    response_text = response.text

    if not response.is_success:
        raise BuildfunctionsError(f"Failed to create sandbox: {response_text}", "UNKNOWN_ERROR", response.status_code)

    try:
        data = json.loads(response_text)
    except json.JSONDecodeError:
        raise BuildfunctionsError(
            f"Invalid JSON response: {response_text}", "UNKNOWN_ERROR", response.status_code
        )

    sandbox_id = data["siteId"]
    sandbox_endpoint = data.get("endpoint") or f"https://{name}.buildfunctions.app"
    sandbox_runtime = config.get("runtime", language)

    return _create_cpu_sandbox_instance(sandbox_id, name, sandbox_runtime, sandbox_endpoint, api_token, base_url)


class CPUSandbox:
    """CPU Sandbox factory - matches TypeScript SDK pattern."""

    @staticmethod
    async def create(config: CPUSandboxConfig) -> DotDict:
        """Create a new CPU sandbox."""
        return await _create_cpu_sandbox(config)


# Alias for direct function call style
create_cpu_sandbox = _create_cpu_sandbox
