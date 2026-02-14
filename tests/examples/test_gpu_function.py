import asyncio
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
async def test_gpu_function():
    """Test GPU function deployment lifecycle."""
    if not API_TOKEN:
        pytest.skip("Set BUILDFUNCTIONS_API_TOKEN in .env file")

    print("Testing GPU Function...\n")

    deployed_function = None
    streaming_function = None

    try:
        # Step 1: Authenticate
        print("1. Authenticating...")
        client = await Buildfunctions({"apiToken": API_TOKEN})
        print(f"   Authenticated as: {client.user.username}")

        # Step 2: Deploy GPU Function
        print("\n2. Deploying GPU Function...")

        deployed_function = await GPUFunction.create({
            "name": f"sdk-gpu-function-{int(time.time())}",
            "code": str(Path(__file__).parent / "gpu_function_code.py"),
            "language": "python",
            "gpu": "T4G",
            "vcpus": 30,
            "memory": "50000MB",
            "timeout": 300,
            "requirements": "transformers==4.47.1\ntorch\naccelerate",
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

        # Step 5: Clean up normal function
        print("\n5. Deleting GPU Function...")
        await deployed_function.delete()
        deployed_function = None
        print("   GPU Function deleted")

        # Step 6: Deploy streaming GPU Function
        print("\n6. Deploying Streaming GPU Function...")

        streaming_function = await GPUFunction.create({
            "name": f"sdk-gpu-func-stream-{int(time.time())}",
            "code": str(Path(__file__).parent / "gpu_function_code_streaming.py"),
            "language": "python",
            "gpu": "T4G",
            "vcpus": 30,
            "memory": "50000MB",
            "timeout": 300,
            "requirements": "torch",
        })

        print("   Streaming GPU Function deployed")
        print(f"   ID: {streaming_function.id}")
        print(f"   Name: {streaming_function.name}")
        print(f"   Endpoint: {streaming_function.endpoint}")

        # Step 7: Wait and call streaming endpoint
        print("\n7. Waiting 10 seconds before calling streaming endpoint...")
        await asyncio.sleep(10)

        print(f"   Calling streaming endpoint: {streaming_function.endpoint}")
        async with httpx.AsyncClient() as http:
            async with http.stream("POST", streaming_function.endpoint, json={"test": True}) as stream_response:
                streamed_text = ""
                chunk_count = 0
                async for chunk in stream_response.aiter_text():
                    streamed_text += chunk
                    chunk_count += 1

        print(f"   Stream status: {stream_response.status_code}")
        print(f"   Chunks received: {chunk_count}")
        print(f"   Streamed text preview: {streamed_text[:200]}{'...' if len(streamed_text) > 200 else ''}")

        if "<<START_STREAM>>" in streamed_text and "<<END_STREAM>>" in streamed_text:
            print("   PASS: Streaming response received with correct markers")
        else:
            print("   WARN: Streaming markers not found in response")

        # Step 8: Clean up streaming function
        print("\n8. Deleting Streaming GPU Function...")
        await streaming_function.delete()
        streaming_function = None
        print("   Streaming GPU Function deleted")

        print("\nGPU Function test completed!")

    except Exception:
        if deployed_function and deployed_function.delete:
            print("Attempting cleanup of normal function...")
            try:
                await deployed_function.delete()
                print("GPU Function cleaned up")
            except Exception as e:
                print(f"Cleanup failed: {e}")

        if streaming_function and streaming_function.delete:
            print("Attempting cleanup of streaming function...")
            try:
                await streaming_function.delete()
                print("Streaming GPU Function cleaned up")
            except Exception as e:
                print(f"Cleanup failed: {e}")
        raise
