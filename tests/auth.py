import os
import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv

from buildfunctions import Buildfunctions

load_dotenv()

API_TOKEN = os.environ.get("BUILDFUNCTIONS_API_TOKEN", "")


@pytest.mark.asyncio
async def test_auth():
    """Test full authentication flow."""
    if not API_TOKEN:
        pytest.skip("Set BUILDFUNCTIONS_API_TOKEN in .env file")

    print("Testing SDK Authentication...\n")

    # Step 1: Authenticate
    print("1. Authenticating...")
    client = await Buildfunctions({"apiToken": API_TOKEN})
    print(f"   Authenticated as: {client.user.username}")
    print(f"   User ID: {client.user.id}")
    print(f"   Session expires: {client.sessionExpiresAt}")

    assert client.user is not None
    assert client.user.id

    # Step 2: List Functions
    print("\n2. Listing Functions...")
    all_functions: list = []
    page = 1
    has_more = True

    while has_more:
        functions = await client.functions.list({"page": page})
        all_functions.extend(functions)
        has_more = len(functions) == 10
        page += 1

    cpu_functions = [f for f in all_functions if not f.get("isGPUF")]
    gpu_functions = [f for f in all_functions if f.get("isGPUF")]

    print(f"   Total Functions: {len(all_functions)}")
    print(f"   CPU Functions: {len(cpu_functions)}")
    print(f"   GPU Functions: {len(gpu_functions)}")

    if all_functions:
        print(f"   Most recent: {all_functions[0].name}")

    # Step 3: List Sandboxes
    print("\n3. Listing Sandboxes...")
    http = client.getHttpClient()
    sandboxes = await http["get"]("/api/sdk/sandbox")
    print(f"   CPU Sandboxes: {sandboxes.get('cpuCount', 0)}")
    print(f"   GPU Sandboxes: {sandboxes.get('gpuCount', 0)}")

    print("\nSDK Authentication test completed!")
