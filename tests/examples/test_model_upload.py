import json
import os
import sys
import time
from pathlib import Path

# Import from local source instead of installed package
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import pytest
from dotenv import load_dotenv

from buildfunctions import Buildfunctions, Model

load_dotenv()

API_TOKEN = os.environ.get("BUILDFUNCTIONS_API_TOKEN", "")


@pytest.mark.asyncio
async def test_model_upload():
    """Test model upload lifecycle."""
    if not API_TOKEN:
        pytest.skip("Set BUILDFUNCTIONS_API_TOKEN in .env file")

    print("Testing Model Upload...\n")

    try:
        # Step 1: Authenticate
        print("1. Authenticating...")
        client = await Buildfunctions({"apiToken": API_TOKEN})
        print(f"   Authenticated as: {client.user.username}")

        # Step 2: Upload model
        print("\n2. Uploading model...")

        model = await Model.create({
            "path": "/path/to/models/Llama-3.2-3B-Instruct-bnb-4bit",
            "name": "remote-model-for-sdk-test",
        })
        print("   Model uploaded")
        print(f"   ID: {model.id}")
        print(f"   Name: {model.name}")

        assert model.id
        assert model.name

        print("\nModel upload test completed!")

    except Exception:
        raise
