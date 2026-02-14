import asyncio
import json
import os
import sys
import time
from pathlib import Path

# Import from local source instead of installed package
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import pytest
from dotenv import load_dotenv

from buildfunctions import Buildfunctions, GPUSandbox

load_dotenv()

API_TOKEN = os.environ.get("BUILDFUNCTIONS_API_TOKEN", "")


@pytest.mark.asyncio
async def test_gpu_sandbox_shared_memory():
    """Test GPU sandbox with shared memory (gpu_count: 2) lifecycle."""
    if not API_TOKEN:
        pytest.skip("Set BUILDFUNCTIONS_API_TOKEN in .env file")

    print("Testing GPU Sandbox with Shared Memory (gpu_count: 2)...\n")

    sandbox = None

    try:
        # Step 1: Authenticate
        print("1. Authenticating...")
        client = await Buildfunctions({"apiToken": API_TOKEN})
        print(f"   Authenticated as: {client.user.username}")

        # Step 2: Create GPU Sandbox with gpu_count: 2
        print("\n2. Creating GPU Sandbox with gpu_count: 2...")

        sandbox = await GPUSandbox.create({
            "name": f"sdk-gpu-sandbox-shared-mem-{int(time.time())}",
            "language": "python",
            "memory": "10000MB",
            "timeout": 300,
            "vcpus": 6,
            "gpu_count": 2,
            "code": "./gpu_sandbox_shared_memory_code.py",
            "requirements": "torch",
        })
        print("   GPU Sandbox created")
        print(f"   ID: {sandbox.id}")
        print(f"   Name: {sandbox.name}")
        print(f"   Endpoint: {sandbox.endpoint}")

        # Step 3: Run GPU Sandbox
        print("\n3. Running GPU Sandbox...")
        result = await sandbox.run()
        print(f"   Response: {json.dumps(dict(result), indent=2, default=str)}")

        # Step 4: Verify GPU memory and device info
        print("\n4. Verifying GPU info...")
        response = result.get("response", result)
        if isinstance(response, str):
            response = json.loads(response)
        data = json.loads(response["body"]) if "body" in response else response

        print(f"   CUDA available: {data.get('cuda_available')}")
        print(f"   Device count: {data.get('device_count')}")

        if data.get("devices"):
            total_memory_mb = 0
            for device in data["devices"]:
                print(f"   Device {device['index']}: {device['name']} - {device['memory_total_mb']}MB total, {device['memory_free_mb']}MB free")
                total_memory_mb += device["memory_total_mb"]
            print(f"   Combined GPU memory: {total_memory_mb}MB across {len(data['devices'])} devices")

        if data.get("device_count", 0) >= 2:
            print("   PASS: Multiple GPU devices detected")
        else:
            print(f"   WARN: Expected 2 devices, got {data.get('device_count')}")

        # Step 5: Delete GPU Sandbox
        print("\n5. Deleting GPU Sandbox...")
        await sandbox.delete()
        sandbox = None
        print("   GPU Sandbox deleted")

        print("\nGPU Sandbox shared memory test completed!")

    except Exception:
        if sandbox and sandbox.delete:
            print("Attempting cleanup...")
            try:
                await sandbox.delete()
                print("GPU Sandbox cleaned up")
            except Exception as e:
                print(f"Cleanup failed: {e}")
        raise
