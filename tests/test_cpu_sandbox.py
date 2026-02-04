import json
import os
import sys
import time
from pathlib import Path

import pytest
from dotenv import load_dotenv

from buildfunctions import Buildfunctions, CPUSandbox

load_dotenv()

API_TOKEN = os.environ.get("BUILDFUNCTIONS_API_TOKEN", "")


@pytest.mark.asyncio
async def test_cpu_sandbox():
    """Test CPU sandbox lifecycle."""
    if not API_TOKEN:
        pytest.skip("Set BUILDFUNCTIONS_API_TOKEN in .env file")

    print("Testing CPU Sandbox...\n")

    sandbox = None

    try:
        # Step 1: Authenticate
        print("1. Authenticating...")
        client = await Buildfunctions({"apiToken": API_TOKEN})
        print(f"   Authenticated as: {client.user.username}")

        # Step 2: Create CPU Sandbox with handler code
        print("\n2. Creating CPU Sandbox...")

        sandbox = await CPUSandbox.create({
            "name": f"sdk-cpu-sandbox-{int(time.time())}",
            "language": "python",
            "code": "/path/to/code/cpu_sandbox_code.py",
            "memory": 128,
            "timeout": 30,
        })
        print("   CPU Sandbox created")
        print(f"   ID: {sandbox.id}")
        print(f"   Name: {sandbox.name}")
        print(f"   Runtime: {sandbox.runtime}")
        print(f"   Endpoint: {sandbox.endpoint}")

        # Step 3: Run CPU Sandbox
        print("\n3. Running CPU Sandbox...")
        result = await sandbox.run()
        print(f"   Result: {json.dumps(dict(result), indent=2, default=str)}")

        # Step 4: Clean up
        print("\n4. Deleting CPU Sandbox...")
        await sandbox.delete()
        print("   CPU Sandbox deleted")

        print("\nCPU Sandbox test completed!")

    except Exception:
        if sandbox and sandbox.delete:
            print("Attempting cleanup...")
            try:
                await sandbox.delete()
                print("CPU Sandbox cleaned up")
            except Exception as e:
                print(f"Cleanup failed: {e}")
        raise
