"""Compliance pre-flight for GPU builds.

GPU functions and GPU sandboxes post straight to the storage/build server,
bypassing every buildfunctions build route — so before we send that request
we ask buildfunctions (which sees the caller's live request geo) whether this
build is allowed from the caller's country. A blocked country raises here,
before anything is provisioned.

The country decision is made server-side from the live request geo — this SDK
only relays the verdict.
"""

from __future__ import annotations

from typing import Any

import httpx

from buildfunctions.errors import error_from_response


async def assert_build_allowed(base_url: str, api_token: str, body: dict[str, Any]) -> None:
    """Raise BuildfunctionsError (451 hard-block / 403 review) when the build is
    not permitted from the caller's country. Returns silently when allowed.

    A network error reaching the check is treated as allow (fail-open) so a
    transient glitch never blocks a legitimate build — matching Layer-1's
    default policy.
    """
    url = f"{base_url.rstrip('/')}/api/sdk/compliance/check-build"

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            response = await client.post(
                url,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_token}",
                },
                json=body or {},
            )
    except httpx.HTTPError:
        return  # fail-open on network error

    if response.is_success:
        return

    # Only the compliance statuses are enforced; anything else falls through to
    # the real build (which will surface its own error).
    if response.status_code in (451, 403):
        try:
            data = response.json()
        except Exception:
            data = {}
        raise error_from_response(response.status_code, data if isinstance(data, dict) else {})
