import os
import sys
from pathlib import Path

# Import from local source instead of installed package
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import pytest
from dotenv import load_dotenv

from buildfunctions import Buildfunctions, Model

load_dotenv()

API_TOKEN = os.environ.get("BUILDFUNCTIONS_API_TOKEN", "")


@pytest.mark.asyncio
async def test_model_delete():
    """Test model find and delete lifecycle."""
    if not API_TOKEN:
        pytest.skip("Set BUILDFUNCTIONS_API_TOKEN in .env file")

    print("Testing Model Delete...\n")

    deployed_model = None

    try:
        # Step 1: Authenticate
        print("1. Authenticating...")
        client = await Buildfunctions({"apiToken": API_TOKEN})
        print(f"   Authenticated as: {client.user.username}")

        # Step 2: Find model
        print("\n2. Finding model...")

        deployed_model = await Model.find_unique({
            "where": {"name": "remote-model-for-sdk-test"}
        })

        if not deployed_model:
            print("   Model not found")
            return

        print("   Model found")
        print(f"   ID: {deployed_model.id}")
        print(f"   Name: {deployed_model.name}")

        # Step 3: Delete model
        print("\n3. Deleting model...")
        await deployed_model.delete()
        print("   Model deleted")
        deployed_model = None

        print("\nModel delete test completed!")

    except Exception:
        if deployed_model and deployed_model.get("delete"):
            print("Attempting cleanup...")
            try:
                await deployed_model.delete()
                print("   Model deleted")
            except Exception as e:
                print(f"Cleanup failed: {e}")
        raise
