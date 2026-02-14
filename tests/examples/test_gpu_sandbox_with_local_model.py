import json
import os
import sys
import time
from pathlib import Path

# Import from local source instead of installed package
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import pytest
from dotenv import load_dotenv

from buildfunctions import Buildfunctions, GPUSandbox, Model

load_dotenv()

API_TOKEN = os.environ.get("BUILDFUNCTIONS_API_TOKEN", "")


@pytest.mark.asyncio
async def test_gpu_sandbox_with_model():
    """Test GPU sandbox with pre-uploaded model lifecycle."""
    if not API_TOKEN:
        pytest.skip("Set BUILDFUNCTIONS_API_TOKEN in .env file")

    print("Testing GPU Sandbox with Model...\n")

    model = None
    sandbox = None
    streaming_sandbox = None

    try:
        # Step 1: Authenticate
        print("1. Authenticating...")
        client = await Buildfunctions({"apiToken": API_TOKEN})
        print(f"   Authenticated as: {client.user.username}")

        # Step 2: Find pre-uploaded model
        print("\n2. Finding model...")
        deployed_model = await Model.find_unique({
            "where": {"name": "remote-model-for-sdk-test"}
        })

        if not deployed_model:
            print("   Model not found — upload it first with test_model_upload.py")
            pytest.skip("Model not uploaded yet")

        print(f"   Model found: {deployed_model.name}")

        # Step 3: Create GPU Sandbox referencing the uploaded model by name
        print("\n3. Creating GPU Sandbox with model reference...")

        sandbox = await GPUSandbox.create({
            "name": f"sdk-gpu-sandbox-model-{int(time.time())}",
            "language": "python",
            "memory": "10000MB",
            "timeout": 300,
            "vcpus": 6,
            "code": "./gpu_sandbox_code.py",
            "model": deployed_model.name,
            "requirements": "torch",
        })
        print("   GPU Sandbox created")
        print(f"   ID: {sandbox.id}")
        print(f"   Name: {sandbox.name}")
        print(f"   Runtime: {sandbox.runtime}")
        print(f"   GPU: {sandbox.gpu}")
        print(f"   Endpoint: {sandbox.endpoint}")

        # Step 4: Run GPU Sandbox
        print("\n4. Running GPU Sandbox...")
        result = await sandbox.run()
        print(f"   Response: {json.dumps(dict(result), indent=2, default=str)}")

        # Step 5: Clean up normal sandbox
        print("\n5. Deleting GPU Sandbox...")
        await sandbox.delete()
        sandbox = None
        print("   GPU Sandbox deleted")

        # Step 6: Create streaming GPU Sandbox
        print("\n6. Creating Streaming GPU Sandbox...")

        streaming_sandbox = await GPUSandbox.create({
            "name": f"sdk-gpu-sb-stream-{int(time.time())}",
            "language": "python",
            "memory": "10000MB",
            "timeout": 300,
            "vcpus": 6,
            "code": "./gpu_function_code_streaming.py",
            "requirements": "torch",
        })
        print("   Streaming GPU Sandbox created")
        print(f"   ID: {streaming_sandbox.id}")
        print(f"   Name: {streaming_sandbox.name}")
        print(f"   Endpoint: {streaming_sandbox.endpoint}")

        # Step 7: Run streaming sandbox
        print("\n7. Running Streaming GPU Sandbox...")
        stream_result = await streaming_sandbox.run()
        stream_response = stream_result.get("response", "")
        if not isinstance(stream_response, str):
            stream_response = json.dumps(stream_response, default=str)

        print(f"   Stream status: {stream_result.get('status')}")
        print(f"   Streamed response preview: {stream_response[:200]}{'...' if len(stream_response) > 200 else ''}")

        if "<<START_STREAM>>" in stream_response or "STREAM_CHUNK" in stream_response:
            print("   PASS: Streaming response received with correct markers")
        else:
            print("   WARN: Streaming markers not found in response")
            print(f"   Full response: {stream_response}")

        # Step 8: Clean up streaming sandbox
        print("\n8. Deleting Streaming GPU Sandbox...")
        await streaming_sandbox.delete()
        streaming_sandbox = None
        print("   Streaming GPU Sandbox deleted")

        print("\nGPU Sandbox with model test completed!")

    except Exception:
        if sandbox and sandbox.delete:
            print("Attempting sandbox cleanup...")
            try:
                await sandbox.delete()
                print("GPU Sandbox cleaned up")
            except Exception as e:
                print(f"Sandbox cleanup failed: {e}")

        if streaming_sandbox and streaming_sandbox.delete:
            print("Attempting streaming sandbox cleanup...")
            try:
                await streaming_sandbox.delete()
                print("Streaming GPU Sandbox cleaned up")
            except Exception as e:
                print(f"Streaming sandbox cleanup failed: {e}")

        if model and model.get("delete"):
            print("Attempting model cleanup...")
            try:
                await model.delete()
                print("Model cleaned up")
            except Exception as e:
                print(f"Model cleanup failed: {e}")
        raise
