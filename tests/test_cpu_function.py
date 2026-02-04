import asyncio
import os
import sys
import time
from pathlib import Path

import httpx
import pytest
from dotenv import load_dotenv

from buildfunctions import Buildfunctions, CPUFunction

load_dotenv()

API_TOKEN = os.environ.get("BUILDFUNCTIONS_API_TOKEN", "")


@pytest.mark.asyncio
async def test_cpu_function():
    """Test CPU function deployment lifecycle."""
    if not API_TOKEN:
        pytest.skip("Set BUILDFUNCTIONS_API_TOKEN in .env file")

    print("Testing CPU Function...\n")

    deployed_function = None

    try:
        # Step 1: Authenticate
        print("1. Authenticating...")
        client = await Buildfunctions({"apiToken": API_TOKEN})
        print(f"   Authenticated as: {client.user.username}")

        # Step 2: Deploy CPU Function
        print("\n2. Deploying CPU Function...")

        deployed_function = await CPUFunction.create({
            "name": f"sdk-cpu-function-{int(time.time())}",
            "code": "./cpu_function_code.py",
            "language": "python",
            "memory": 128,
            "timeout": 30,
        })

        print("   CPU Function deployed")
        print(f"   ID: {deployed_function.id}")
        print(f"   Name: {deployed_function.name}")
        print(f"   Endpoint: {deployed_function.endpoint}")

        assert deployed_function.id
        assert deployed_function.endpoint

        # Step 3: Verify CPU Function exists in list
        print("\n3. Verifying CPU Function in list...")
        functions = await client.functions.list()
        found = next((f for f in functions if f.id == deployed_function.id), None)

        if found:
            print("   CPU Function found in list")
        else:
            print("   CPU Function not found in list (may take a moment)")

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
        print("\n5. Deleting CPU Function...")
        await deployed_function.delete()
        print("   CPU Function deleted")

        print("\nCPU Function test completed!")

    except Exception:
        if deployed_function and deployed_function.delete:
            print("Attempting cleanup...")
            try:
                await deployed_function.delete()
                print("CPU Function cleaned up")
            except Exception as e:
                print(f"Cleanup failed: {e}")
        raise
