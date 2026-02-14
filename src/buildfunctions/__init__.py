"""Buildfunctions SDK - Python SDK for the serverless platform for AI agents.

Example:
    import asyncio
    from buildfunctions import Buildfunctions, CPUSandbox, GPUSandbox, GPUFunction

    async def main():
        # Initialize the client (authenticates with the API)
        client = await Buildfunctions({
            "apiToken": os.environ["BUILDFUNCTIONS_API_TOKEN"]
        })

        # Access authenticated user info (supports both dot and bracket notation)
        print(client.user.username)
        print(client.authenticatedAt)

        # Create a CPU sandbox
        sandbox = await CPUSandbox.create({
            "name": "my-sandbox",
            "language": "python",
            "memory": "512MB",
        })

        # Run code
        result = await sandbox.run()
        print(result.response)

        # Clean up
        await sandbox.delete()

    asyncio.run(main())
"""

# Client exports - match TypeScript SDK naming exactly
from buildfunctions.client import (
    Buildfunctions,
    buildfunctions,
    createClient,
    create_client,
    init,
)

# Function builders - match TypeScript SDK naming exactly
from buildfunctions.cpu_function import CPUFunction, create_cpu_function
from buildfunctions.gpu_function import GPUFunction, create_gpu_function

# Sandbox factories - match TypeScript SDK naming exactly
from buildfunctions.cpu_sandbox import CPUSandbox, create_cpu_sandbox
from buildfunctions.gpu_sandbox import GPUSandbox, create_gpu_sandbox

# Model factory
from buildfunctions.model import Model, create_model, set_model_api_token

# Errors
from buildfunctions.errors import (
    AuthenticationError,
    BuildfunctionsError,
    CapacityError,
    NotFoundError,
    ValidationError,
)

# Types
from buildfunctions.types import (
    AuthenticatedUser,
    AuthResponse,
    BuildfunctionsConfig,
    CPUFunctionOptions,
    CPUSandboxConfig,
    CPUSandboxInstance,
    CreateFunctionOptions,
    DeployedFunction,
    ErrorCode,
    FileMetadata,
    FindUniqueOptions,
    Framework,
    FunctionConfig,
    GPUFunctionOptions,
    GPUSandboxConfig,
    GPUSandboxInstance,
    GPUType,
    Language,
    ListOptions,
    Memory,
    ModelConfig,
    ModelInstance,
    RunResult,
    Runtime,
    SandboxInstance,
    UploadOptions,
)

__all__ = [
    # Client (PascalCase - matches TypeScript)
    "Buildfunctions",
    "createClient",
    "init",
    # Client (snake_case aliases)
    "buildfunctions",
    "create_client",
    # Function builders (PascalCase - matches TypeScript)
    "CPUFunction",
    "GPUFunction",
    # Function builders (snake_case aliases)
    "create_cpu_function",
    "create_gpu_function",
    # Sandbox factories (PascalCase - matches TypeScript)
    "CPUSandbox",
    "GPUSandbox",
    # Sandbox factories (snake_case aliases)
    "create_cpu_sandbox",
    "create_gpu_sandbox",
    # Model factory
    "Model",
    "create_model",
    # Errors
    "BuildfunctionsError",
    "AuthenticationError",
    "NotFoundError",
    "ValidationError",
    "CapacityError",
    # Types
    "BuildfunctionsConfig",
    "AuthenticatedUser",
    "AuthResponse",
    "Language",
    "Runtime",
    "GPUType",
    "Framework",
    "Memory",
    "FunctionConfig",
    "CPUFunctionOptions",
    "GPUFunctionOptions",
    "CreateFunctionOptions",
    "DeployedFunction",
    "CPUSandboxConfig",
    "GPUSandboxConfig",
    "RunResult",
    "UploadOptions",
    "SandboxInstance",
    "CPUSandboxInstance",
    "GPUSandboxInstance",
    "FindUniqueOptions",
    "ListOptions",
    "ErrorCode",
    "FileMetadata",
    "ModelConfig",
    "ModelInstance",
]
