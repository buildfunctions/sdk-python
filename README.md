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

> Hardware-isolated execution environments for AI agents

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

The SDK is currently in beta. If you encounter any issues or have specific syntax requirements, please reach out and contact us at team@buildfunctions.com, and we’ll work to address them.
