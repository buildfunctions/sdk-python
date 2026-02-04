"""HTTP Client for Buildfunctions API."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode, urljoin

import httpx

from buildfunctions.errors import AuthenticationError, BuildfunctionsError, error_from_response


def create_http_client(base_url: str, api_token: str, timeout: float = 600.0) -> dict[str, Any]:
    """Create an HTTP client for the Buildfunctions API.

    Returns a dict with request/get/post/put/delete/set_token functions.
    """
    if not api_token:
        raise AuthenticationError("API token is required")

    resolved_base_url = base_url.rstrip("/")
    state = {"token": api_token}

    def _build_url(path: str, params: dict[str, str | int] | None = None) -> str:
        url = f"{resolved_base_url}{path}"
        if params:
            query = urlencode({k: str(v) for k, v in params.items()})
            url = f"{url}?{query}"
        return url

    async def _parse_response(response: httpx.Response) -> Any:
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            return response.json()
        text = response.text
        return {"message": text}

    async def request(
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        params: dict[str, str | int] | None = None,
    ) -> Any:
        url = _build_url(path, params)
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {state['token']}",
        }

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
                response = await client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    json=body,
                )

            data = await _parse_response(response)

            if not response.is_success:
                raise error_from_response(response.status_code, data if isinstance(data, dict) else {})

            return data

        except BuildfunctionsError:
            raise
        except httpx.TimeoutException:
            raise BuildfunctionsError("Request timed out", "NETWORK_ERROR")
        except httpx.ConnectError:
            raise BuildfunctionsError("Unable to connect to server", "NETWORK_ERROR")
        except Exception:
            raise BuildfunctionsError("Request failed", "UNKNOWN_ERROR")

    async def get(path: str, params: dict[str, str | int] | None = None) -> Any:
        return await request("GET", path, params=params)

    async def post(path: str, body: dict[str, Any] | None = None) -> Any:
        return await request("POST", path, body=body)

    async def put(path: str, body: dict[str, Any] | None = None) -> Any:
        return await request("PUT", path, body=body)

    async def delete(path: str, body: dict[str, Any] | None = None) -> Any:
        return await request("DELETE", path, body=body)

    def set_token(token: str) -> None:
        state["token"] = token

    return {
        "request": request,
        "get": get,
        "post": post,
        "put": put,
        "delete": delete,
        "set_token": set_token,
    }
