"""Internal endpoint selection helpers for private dev routing."""

from __future__ import annotations

import os

DEFAULT_GPU_BUILD_URL = "https://prod-gpu-build-server.buildfunctions.link"

_DEFAULT_DEV_GPU_BUILD_URL = "https://dev-gpu-build-server.buildfunctions.link"
_TEST_ACCOUNT_ENV = "TEST_ACCOUNT"


def _should_use_internal_dev_gpu_build_url(
    user_id: str | None = None,
    username: str | None = None,
    email: str | None = None,
) -> bool:
    test_account = os.environ.get(_TEST_ACCOUNT_ENV, "").strip()
    if not test_account:
        return False

    normalized_test_account = test_account.lower()
    return (
        bool(user_id and user_id.strip() == test_account)
        or bool(username and username.strip().lower() == normalized_test_account)
        or bool(email and email.strip().lower() == normalized_test_account)
    )


def resolve_gpu_build_url(
    explicit_gpu_build_url: str | None = None,
    *,
    user_id: str | None = None,
    username: str | None = None,
    email: str | None = None,
) -> str:
    if explicit_gpu_build_url:
        return explicit_gpu_build_url

    if not _should_use_internal_dev_gpu_build_url(user_id=user_id, username=username, email=email):
        return DEFAULT_GPU_BUILD_URL

    return _DEFAULT_DEV_GPU_BUILD_URL
