import asyncio
import json
import os
import sys
import time
from pathlib import Path

# Import from local source instead of installed package
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import httpx
import pytest
from dotenv import load_dotenv

from buildfunctions import Buildfunctions, GPUFunction

load_dotenv()

API_TOKEN = os.environ.get("BUILDFUNCTIONS_API_TOKEN", "")


@pytest.mark.asyncio
async def test_gpu_function_shared_memory():
    """Test GPU function with shared memory (gpu_count: 2) lifecycle."""
    if not API_TOKEN:
        pytest.skip("Set BUILDFUNCTIONS_API_TOKEN in .env file")

    print("Testing GPU Function with Shared Memory (gpu_count: 2)...\n")

    deployed_function = None

    try:
        # Step 1: Authenticate
        print("1. Authenticating...")
        client = await Buildfunctions({"apiToken": API_TOKEN})
        print(f"   Authenticated as: {client.user.username}")

        # Step 2: Deploy GPU Function with gpu_count: 2
        print("\n2. Deploying GPU Function with gpu_count: 2...")

        deployed_function = await GPUFunction.create({
            "name": f"sdk-gpu-func-shared-mem-{int(time.time())}",
            "code": str(Path(__file__).parent / "gpu_function_shared_memory_code.py"),
            "language": "python",
            "gpu": "T4G",
            "vcpus": 6,
            "gpu_count": 2,
            "memory": "10000MB",
            "timeout": 300,
            "requirements": "torch",
        })

        print("   GPU Function deployed")
        print(f"   ID: {deployed_function.id}")
        print(f"   Name: {deployed_function.name}")
        print(f"   Endpoint: {deployed_function.endpoint}")

        assert deployed_function.id

        # Step 3: Verify GPU Function exists in list
        print("\n3. Verifying GPU Function in list...")
        functions = await client.functions.list()
        found = next((f for f in functions if f.id == deployed_function.id), None)

        if found:
            print("   GPU Function found in list")
            print(f"   Is GPU: {found.isGPUF}")
        else:
            print("   GPU Function not found in list (may take a moment)")

        # Step 4: Wait and call the endpoint
        print("\n4. Waiting 10 seconds before calling endpoint...")
        await asyncio.sleep(10)

        endpoint = deployed_function.endpoint
        print(f"   Calling endpoint: {endpoint}")
        async with httpx.AsyncClient() as http:
            response = await http.post(endpoint, json={"test": True})
            print(f"   Status: {response.status_code}")
            print(f"   Response: {response.text}")

        # Step 5: Verify GPU memory and device info
        print("\n5. Verifying GPU info...")
        try:
            parsed = response.json()
            data = json.loads(parsed["body"]) if "body" in parsed else parsed

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
        except Exception as e:
            print(f"   Could not parse response for GPU verification: {e}")

        # Step 6: Delete GPU Function
        print("\n6. Deleting GPU Function...")
        await deployed_function.delete()
        deployed_function = None
        print("   GPU Function deleted")

        print("\nGPU Function shared memory test completed!")

    except Exception:
        if deployed_function and deployed_function.delete:
            print("Attempting cleanup...")
            try:
                await deployed_function.delete()
                print("GPU Function cleaned up")
            except Exception as e:
                print(f"Cleanup failed: {e}")
        raise
