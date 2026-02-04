import json
import os
import sys
import time
from pathlib import Path

import pytest
from dotenv import load_dotenv

from buildfunctions import Buildfunctions, GPUSandbox

load_dotenv()

API_TOKEN = os.environ.get("BUILDFUNCTIONS_API_TOKEN", "")


@pytest.mark.asyncio
async def test_gpu_sandbox_with_model():
    """Test GPU sandbox with local model lifecycle."""
    if not API_TOKEN:
        pytest.skip("Set BUILDFUNCTIONS_API_TOKEN in .env file")

    print("Testing GPU Sandbox with Local Model...\n")

    sandbox = None

    try:
        # Step 1: Authenticate
        print("1. Authenticating...")
        client = await Buildfunctions({"apiToken": API_TOKEN})
        print(f"   Authenticated as: {client.user.username}")

        # Step 2: Create GPU Sandbox with handler code and local model
        print("\n2. Creating GPU Sandbox with local model...")

        sandbox = await GPUSandbox.create({
            "name": f"sdk-gpu-sandbox-model-{int(time.time())}",
            "language": "python",
            "memory": 10000,
            "timeout": 300,
            "vcpus": 6,
            "code": "./gpu_sandbox_code.py",
            "model": "/path/to/models/Qwen/Qwen3-8B",
            "requirements": "torch",
        })
        print("   GPU Sandbox created")
        print(f"   ID: {sandbox.id}")
        print(f"   Name: {sandbox.name}")
        print(f"   Runtime: {sandbox.runtime}")
        print(f"   GPU: {sandbox.gpu}")
        print(f"   Endpoint: {sandbox.endpoint}")

        # Step 3: Run GPU Sandbox
        print("\n3. Running GPU Sandbox...")
        result = await sandbox.run()
        print(f"   Response: {json.dumps(dict(result), indent=2, default=str)}")

        # Step 4: Clean up
        print("\n4. Deleting GPU Sandbox...")
        await sandbox.delete()
        print("   GPU Sandbox deleted")

        print("\nGPU Sandbox with local model test completed!")

    except Exception:
        if sandbox and sandbox.delete:
            print("Attempting cleanup...")
            try:
                await sandbox.delete()
                print("GPU Sandbox cleaned up")
            except Exception as e:
                print(f"Cleanup failed: {e}")
        raise
