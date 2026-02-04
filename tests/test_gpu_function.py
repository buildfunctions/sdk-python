import asyncio
import os
import sys
import time
from pathlib import Path

import httpx
import pytest
from dotenv import load_dotenv

from buildfunctions import Buildfunctions, GPUFunction

load_dotenv()

API_TOKEN = os.environ.get("BUILDFUNCTIONS_API_TOKEN", "")


@pytest.mark.asyncio
async def test_gpu_function():
    """Test GPU function deployment lifecycle."""
    if not API_TOKEN:
        pytest.skip("Set BUILDFUNCTIONS_API_TOKEN in .env file")

    print("Testing GPU Function...\n")

    deployed_function = None

    try:
        # Step 1: Authenticate
        print("1. Authenticating...")
        client = await Buildfunctions({"apiToken": API_TOKEN})
        print(f"   Authenticated as: {client.user.username}")

        # Step 2: Deploy GPU Function
        print("\n2. Deploying GPU Function...")

        deployed_function = await GPUFunction.create({
            "name": f"sdk-gpu-function-{int(time.time())}",
            "code": "/path/to/code/gpu_function_code.py",
            "language": "python",
            "gpu": "T4",
            "vcpus": 30,
            "memory": "50000MB",
            "timeout": 300,
            "requirements": ["transformers==4.47.1", "torch", "accelerate"],
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

        # Step 5: Clean up
        print("\n5. Deleting GPU Function...")
        await deployed_function.delete()
        print("   GPU Function deleted")

        print("\nGPU Function test completed!")

    except Exception:
        if deployed_function and deployed_function.delete:
            print("Attempting cleanup...")
            try:
                await deployed_function.delete()
                print("GPU Function cleaned up")
            except Exception as e:
                print(f"Cleanup failed: {e}")
        raise
