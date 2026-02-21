"""Buildfunctions SDK Type Definitions."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Literal, TypedDict

from buildfunctions.dotdict import DotDict


# Scalar types
Language = Literal["javascript", "typescript", "python", "go", "shell"]
Runtime = Literal["node", "deno", "python", "go", "shell"]
GPUType = Literal["T4G", "T4"]
Framework = Literal["pytorch"]
Memory = Literal["128Mi", "256Mi", "512Mi", "1Gi", "2Gi", "4Gi", "8Gi", "16Gi", "32Gi", "64Gi"]
ErrorCode = Literal[
    "UNAUTHORIZED",
    "NOT_FOUND",
    "INVALID_REQUEST",
    "MAX_CAPACITY",
    "SIZE_LIMIT_EXCEEDED",
    "VALIDATION_ERROR",
    "NETWORK_ERROR",
    "UNKNOWN_ERROR",
]

# Runtime controls event stream
RuntimeControlEventType = Literal[
    "retry",
    "loop_warning",
    "loop_quarantine",
    "loop_stop",
    "circuit_open",
    "budget_stop",
    "policy_denied",
    "policy_approval_required",
    "policy_approved",
    "policy_dry_run",
    "verifier_rejected",
    "idempotency_replay",
    "concurrency_wait",
    "concurrency_rejected",
]


class RuntimeControlEvent(TypedDict, total=False):
    type: RuntimeControlEventType
    message: str
    timestamp: int
    request: dict[str, Any]
    details: dict[str, Any]


class RetryBackoffConfig(TypedDict, total=False):
    maxAttempts: int
    initialDelayMs: int
    maxDelayMs: int
    backoffFactor: float
    jitterRatio: float


class LoopBreakerConfig(TypedDict, total=False):
    enabled: bool
    warningThreshold: int
    quarantineThreshold: int
    stopThreshold: int
    quarantineMs: int
    stopCooldownMs: int
    maxFingerprints: int


RuntimePolicyMode = Literal["enforce", "dryRun"]
RuntimePolicyAction = Literal["allow", "deny", "require_approval"]
ToolConcurrencyWaitMode = Literal["reject", "wait"]


class ToolCircuitBreakerConfig(TypedDict, total=False):
    enabled: bool
    windowMs: int
    minRequests: int
    failureRateThreshold: float
    cooldownMs: int


class ToolPolicyRule(TypedDict, total=False):
    id: str
    action: RuntimePolicyAction
    tools: list[str]
    destinations: list[str]
    actionPrefixes: list[str]
    reason: str


class ToolPolicyGateConfig(TypedDict, total=False):
    enabled: bool
    mode: RuntimePolicyMode
    rules: list[ToolPolicyRule]
    approvalHandler: Callable[[dict[str, Any]], bool | Awaitable[bool]]


class ToolRuntimeStateAdapter(TypedDict, total=False):
    get: Callable[[str], Any | Awaitable[Any]]
    set: Callable[[str, Any], Any | Awaitable[Any]]
    delete: Callable[[str], Any | Awaitable[Any]]
    keys: Callable[[], Any | Awaitable[Any]]


class ToolRuntimeStateAdaptersConfig(TypedDict, total=False):
    loop: ToolRuntimeStateAdapter
    circuit: ToolRuntimeStateAdapter
    budget: ToolRuntimeStateAdapter
    lock: ToolRuntimeStateAdapter
    idempotency: ToolRuntimeStateAdapter


class ToolCallContext(TypedDict, total=False):
    toolName: str
    runKey: str
    destination: str
    action: str
    args: Any
    idempotencyKey: str
    resourceKey: str
    timeoutMs: int
    signal: Any


class ToolRuntimeOverrideConfig(TypedDict, total=False):
    timeoutMs: int
    retry: RetryBackoffConfig
    loopBreaker: LoopBreakerConfig
    circuitBreaker: ToolCircuitBreakerConfig


class ToolRuntimeOverridesConfig(TypedDict, total=False):
    tools: dict[str, ToolRuntimeOverrideConfig]
    destinations: dict[str, ToolRuntimeOverrideConfig]


class ToolIdempotencyConfig(TypedDict, total=False):
    enabled: bool
    ttlMs: int
    includeErrors: bool
    namespaceByRunKey: bool


class ToolConcurrencyConfig(TypedDict, total=False):
    enabled: bool
    leaseMs: int
    waitMode: ToolConcurrencyWaitMode
    waitTimeoutMs: int
    pollIntervalMs: int


class ToolRuntimeVerifiersConfig(TypedDict, total=False):
    beforeCall: Callable[[dict[str, Any]], bool | dict[str, Any] | Awaitable[bool | dict[str, Any]]]
    afterSuccess: Callable[[dict[str, Any]], bool | dict[str, Any] | Awaitable[bool | dict[str, Any]]]
    afterError: Callable[[dict[str, Any]], bool | dict[str, Any] | Awaitable[bool | dict[str, Any]]]


class ToolRuntimeControlsConfig(TypedDict, total=False):
    tenantKey: str
    timeoutMs: int
    maxToolCalls: int
    retry: RetryBackoffConfig
    retryClassifier: Callable[[dict[str, Any]], Any | Awaitable[Any]]
    loopBreaker: LoopBreakerConfig
    circuitBreaker: ToolCircuitBreakerConfig
    policy: ToolPolicyGateConfig
    verifiers: ToolRuntimeVerifiersConfig
    idempotency: ToolIdempotencyConfig
    concurrency: ToolConcurrencyConfig
    overrides: ToolRuntimeOverridesConfig
    state: ToolRuntimeStateAdaptersConfig
    onEvent: Callable[[RuntimeControlEvent], Any]
    eventSinks: list[Callable[[RuntimeControlEvent], Any | Awaitable[Any]]]
    onEventSinkFailure: Callable[[dict[str, Any]], Any]


# Client configuration
class BuildfunctionsConfig(TypedDict, total=False):
    api_token: str
    base_url: str
    gpu_build_url: str


class _BuildfunctionsConfigRequired(TypedDict):
    api_token: str


# Authenticated user
class AuthenticatedUser(TypedDict, total=False):
    id: str
    username: str | None
    email: str | None
    compute_tier: str | None


# Auth response
class AuthResponse(TypedDict):
    authenticated: bool
    user: AuthenticatedUser
    sessionToken: str
    expiresAt: str
    authenticatedAt: str


# Function configuration
class FunctionConfig(TypedDict, total=False):
    memory: str | int
    timeout: int
    cpu_cores: int


# CPU function options
class CPUFunctionOptions(TypedDict, total=False):
    name: str
    language: Language
    runtime: Runtime
    code: str  # Inline code string or path to file (absolute, relative, or ~/path)
    config: FunctionConfig
    env_variables: dict[str, str]
    dependencies: str
    cron_schedule: str


# GPU function options (extends CPU)
class GPUFunctionOptions(TypedDict, total=False):
    name: str
    language: Language
    runtime: Runtime
    code: str  # Inline code string or path to file (absolute, relative, or ~/path)
    config: FunctionConfig
    env_variables: dict[str, str]
    dependencies: str
    cron_schedule: str
    gpu: GPUType
    vcpus: int  # vCPUs for the GPU function VM (hotplugged at runtime, default 10, max 50)
    gpu_count: int  # Number of GPUs (default 1)
    memory: str | int  # "2GB", "1024MB", or number in MB (top-level shorthand for config.memory)
    timeout: int  # Top-level shorthand for config.timeout
    requirements: str | list[str]  # Top-level shorthand for dependencies
    framework: Framework
    model_path: str
    model_name: str


# Create function options (for SDK deploy via client)
class CreateFunctionOptions(TypedDict, total=False):
    name: str
    code: str  # Inline code string or path to file (absolute, relative, or ~/path)
    language: Language
    runtime: Runtime
    memory: str | int
    timeout: int
    env_variables: list[dict[str, str]]
    requirements: str | list[str]
    cron_schedule: str
    processor_type: Literal["CPU", "GPU"]
    framework: Framework
    gpu: GPUType
    model_name: str
    model_path: str


# Deployed function
class DeployedFunction(TypedDict, total=False):
    id: str
    name: str
    subdomain: str
    endpoint: str
    url: str
    language: str
    runtime: str
    memoryAllocated: int
    timeoutSeconds: int
    cpuCores: str
    isGPUF: bool
    framework: str
    createdAt: str
    updatedAt: str
    delete: Callable[[], Awaitable[None]]


# Sandbox configuration
class CPUSandboxConfig(TypedDict, total=False):
    name: str
    language: Language
    runtime: Runtime
    code: str  # Inline code string or path to file (absolute, relative, or ~/path)
    memory: str | int
    timeout: int
    env_variables: list[dict[str, str]]
    requirements: str | list[str]


class GPUSandboxConfig(TypedDict, total=False):
    name: str
    language: Language
    runtime: Runtime
    code: str  # Inline code string or path to file (absolute, relative, or ~/path)
    memory: str | int
    timeout: int
    env_variables: list[dict[str, str]]
    requirements: str | list[str]
    gpu: GPUType
    cpu_cores: int  # vCPUs for the GPU sandbox VM (hotplugged at runtime, default 10, max 50)
    gpu_count: int  # Number of GPUs (default 1)
    model: str | dict[str, str]


# Run result - uses DotDict so both result["response"] and result.response work
class RunResult(DotDict):
    response: Any   # The response (parsed JSON object, or raw string if not JSON)
    status: int     # HTTP status code


# Upload options
class UploadOptions(TypedDict, total=False):
    local_path: str
    file_path: str


# Sandbox instances
class SandboxInstance(TypedDict, total=False):
    id: str
    name: str
    runtime: str
    endpoint: str
    run: Callable[..., Awaitable[RunResult]]
    upload: Callable[[UploadOptions], Awaitable[None]]
    delete: Callable[[], Awaitable[None]]


class CPUSandboxInstance(TypedDict, total=False):
    id: str
    name: str
    runtime: str
    endpoint: str
    type: Literal["cpu"]
    run: Callable[..., Awaitable[RunResult]]
    upload: Callable[[UploadOptions], Awaitable[None]]
    delete: Callable[[], Awaitable[None]]


class GPUSandboxInstance(TypedDict, total=False):
    id: str
    name: str
    runtime: str
    endpoint: str
    type: Literal["gpu"]
    gpu: GPUType
    run: Callable[..., Awaitable[RunResult]]
    upload: Callable[[UploadOptions], Awaitable[None]]
    delete: Callable[[], Awaitable[None]]


# Find options
class FindUniqueWhere(TypedDict, total=False):
    name: str
    id: str


class FindUniqueOptions(TypedDict):
    where: FindUniqueWhere


class ListOptions(TypedDict, total=False):
    page: int


# File metadata (for uploads)
class FileMetadata(TypedDict):
    name: str
    size: int
    type: str
    webkit_relative_path: str
    local_path: str


# Presigned URL info
class PresignedUrlInfo(TypedDict, total=False):
    signedUrl: list[str]
    uploadId: str | None
    numberOfParts: int
    s3FilePath: str


# Model resource types
class ModelConfig(TypedDict, total=False):
    path: str
    name: str


class ModelInstance(TypedDict, total=False):
    id: str
    name: str
    delete: Callable[[], Awaitable[None]]
