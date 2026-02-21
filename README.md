<p align="center">
  <h1 align="center">
  <a href="https://www.buildfunctions.com" target="_blank">
    <img src="./static/readme/buildfunctions-header.svg" alt="logo" width="900">
  </a>
  </h1>
</p>

<h1 align="center">The Buildfunctions SDK for Agents</h1>

<p align="center">
  <!-- <a href="https://discord.com/users/buildfunctions" target="_blank">
    <img src="./static/readme/discord-button.png" height="32" />
  </a>&nbsp; -->
  <a href="https://www.buildfunctions.com/docs/sdk/quickstart" target="_blank">
    <img src="./static/readme/read-the-docs-button.png" height="32" />
  </a>&nbsp;
</p>

<p align="center">
<a href="https://pypi.org/project/buildfunctions" target="_blank">
  <img src="https://img.shields.io/badge/pypi-buildfunctions-green">
</a>
</p>

<p align="center">
  <h1 align="center">
  <a href="https://www.buildfunctions.com" target="_blank">
    <img src="./static/readme/buildfunctions-logo-and-servers-dark.svg" alt="logo" width="900">
  </a>
  </h1>
</p>

> Hardware-isolated execution environments for AI agents — with runtime controls to help keep unattended runs bounded

## Installation

```bash
pip install buildfunctions
```

## Quick Start

### 1. Create an API Token

Get your API token at [buildfunctions.com/settings](https://www.buildfunctions.com/settings)

### 2. CPU Function

```python
from buildfunctions import Buildfunctions, CPUFunction

client = await Buildfunctions({"apiToken": API_TOKEN})

deployed_function = await CPUFunction.create({
    "name": "my-cpu-function",
    "code": "./cpu_function_code.py",
    "language": "python",
    "memory": 128,
    "timeout": 30,
})

print(f"Endpoint: {deployed_function.endpoint}")

await deployed_function.delete()
```

### 3. CPU Sandbox

```python
from buildfunctions import Buildfunctions, CPUSandbox

client = await Buildfunctions({"apiToken": API_TOKEN})

sandbox = await CPUSandbox.create({
    "name": "my-cpu-sandbox",
    "language": "python",
    "code": "/path/to/code/cpu_sandbox_code.py",
    "memory": 128,
    "timeout": 30,
})

result = await sandbox.run()
print(f"Result: {result}")

await sandbox.delete()
```

### 4. GPU Function

```python
from buildfunctions import Buildfunctions, GPUFunction

client = await Buildfunctions({"apiToken": API_TOKEN})

deployed_function = await GPUFunction.create({
    "name": "my-gpu-function",
    "code": "/path/to/code/gpu_function_code.py",
    "language": "python",
    "gpu": "T4",
    "vcpus": 30,
    "memory": "50000MB",
    "timeout": 300,
    "requirements": ["transformers==4.47.1", "torch", "accelerate"],
})

print(f"Endpoint: {deployed_function.endpoint}")

await deployed_function.delete()
```

### 5. GPU Sandbox with Local Model

```python
from buildfunctions import Buildfunctions, GPUSandbox

client = await Buildfunctions({"apiToken": API_TOKEN})

sandbox = await GPUSandbox.create({
    "name": "my-gpu-sandbox",
    "language": "python",
    "memory": 10000,
    "timeout": 300,
    "vcpus": 6,
    "code": "./gpu_sandbox_code.py",
    "model": "/path/to/models/Qwen/Qwen3-8B",
    "requirements": "torch",
})

result = await sandbox.run()
print(f"Response: {result}")

await sandbox.delete()
```

## Runtime Controls: Help Keep Your Agent Running Unattended

Wrap any tool call with composable guardrails — no API key required, no sandbox needed. RuntimeControls works standalone around your own functions, or combined with Buildfunctions sandboxes.

**Available control layers (configure per workflow):**
retries with backoff, per-run tool-call budgets, circuit breakers, loop detection, timeout + cancellation, policy gates, injection guards, idempotency, concurrency locks, and event-based observability via event sinks.

### 1. Wrap Any Tool Call (No API Key)

```python
import httpx
from buildfunctions import RuntimeControls

controls = RuntimeControls.create({
    "maxToolCalls": 50,
    "timeoutMs": 30_000,
    "retry": {"maxAttempts": 3, "initialDelayMs": 200, "backoffFactor": 2},
    "loopBreaker": {"warningThreshold": 5, "quarantineThreshold": 8, "stopThreshold": 12},
    "onEvent": lambda event: print(f"[controls] {event['type']}: {event['message']}"),
})

# Wrap any function — an API call, a shell command, an LLM tool invocation
async def run_api(args, runtime):
    payload = args[0]
    async with httpx.AsyncClient() as client:
        response = await client.post("https://api.example.com/data", json=payload)
        return response.json()

guarded_fetch = controls.wrap({
    "toolName": "api-call",
    "runKey": "agent-run-1",
    "destination": "https://api.example.com",
    "run": run_api,
})

result = await guarded_fetch({"query": "latest results"})
print(result)

# Reset budget counters when starting a new run
await controls.reset("agent-run-1")
```

### 2. With Hardware-Isolated Sandbox + Agent Safety

```python
import re
from buildfunctions import Buildfunctions, CPUSandbox, RuntimeControls, applyAgentLogicSafety

await Buildfunctions({"apiToken": API_TOKEN})

sandbox = await CPUSandbox.create({
    "name": "guarded-sandbox",
    "language": "python",
    "code": "./my_handler.py",
    "memory": 128,
    "timeout": 30,
})

controls = RuntimeControls.create(
    applyAgentLogicSafety(
        {
            "maxToolCalls": 20,
            "retry": {"maxAttempts": 2, "initialDelayMs": 200, "backoffFactor": 2},
            "onEvent": lambda event: print(f"[controls] {event['type']}: {event['message']}"),
        },
        {
            "injectionGuard": {
                "enabled": True,
                "patterns": [
                    re.compile(r"ignore\s+previous\s+instructions", re.I),
                    re.compile(r"\brm\s+-rf\b", re.I),
                ],
            },
        },
    )
)

async def run_sandbox(runtime):
    _ = runtime
    return await sandbox.run()

result = await controls.run(
    {
        "toolName": "cpu-sandbox-run",
        "runKey": "sandbox-run-1",
        "destination": sandbox.endpoint,
        "action": "execute",
    },
    run_sandbox,
)

print(f"Result: {result}")
await sandbox.delete()
```

Full runtime controls documentation: https://www.buildfunctions.com/docs/runtime-controls

Runtime controls are provided as best-effort tools to help manage application behavior and resource usage. They do not guarantee prevention of all unintended outcomes. Users are responsible for monitoring their own workloads. See our [Terms of Service](https://www.buildfunctions.com/terms-of-service) for full details.

By using this SDK, you agree to the [Terms of Service](https://www.buildfunctions.com/terms-of-service).

The SDK is currently in beta. If you encounter any issues or have specific syntax requirements, please reach out and contact us at team@buildfunctions.com, and we’ll work to address them.
