from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

APP_NAME = "Sounio Stroke Lab"
PIPELINE_VERSION = "common-preprocess-v1"
DATASET_VERSION = "manifest-benchmark-v1"
FAIRNESS_POLICY = (
    "same splits, same preprocessing, same atlas registration, same compute budget; "
    "only representation/model stack changes"
)
DEFAULT_TARGET_SHAPE = (32, 64, 64)
ATLAS_REGIONS = (
    "caudate",
    "lentiform",
    "internal_capsule",
    "insula",
    "m1",
    "m2",
    "m3",
    "m4",
    "m5",
    "m6",
)


def get_storage_root() -> Path:
    raw = os.getenv("SOUNIO_STROKE_DATA_DIR")
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path.cwd() / ".sounio-stroke-lab").resolve()


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class MinioSettings:
    endpoint: str
    access_key: str
    secret_key: str
    bucket: str
    secure: bool = False


@dataclass(frozen=True)
class KubernetesSettings:
    namespace: str
    local_queue: str
    worker_image: str
    dataset_pvc: str
    db_secret_name: str
    db_secret_key: str
    minio_secret_name: str
    minio_access_key_key: str
    minio_secret_key_key: str
    service_account_name: str = "sounio-stroke-runner"
    cpu_request: str = "1000m"
    memory_request: str = "1Gi"
    cpu_limit: str = "2000m"
    memory_limit: str = "2Gi"
    shared_path_prefixes: tuple[str, ...] = ("/datasets",)


@dataclass(frozen=True)
class AuthSettings:
    tokens: tuple[str, ...]
    exempt_paths: tuple[str, ...] = ("/health", "/readyz")

    @property
    def enabled(self) -> bool:
        return bool(self.tokens)


@dataclass(frozen=True)
class RuntimeConfig:
    storage_root: Path
    db_url: str | None
    object_store: str
    job_backend: str
    agent_backend: str
    minio: MinioSettings | None
    auth: AuthSettings
    start_embedded_benchmark_worker: bool


def get_db_url() -> str | None:
    raw = os.getenv("SOUNIO_STROKE_DB_URL", "").strip()
    return raw or None


def get_object_store_backend() -> str:
    return os.getenv("SOUNIO_STROKE_OBJECT_STORE", "filesystem").strip().lower()


def get_job_backend_name() -> str:
    return os.getenv("SOUNIO_STROKE_JOB_BACKEND", "local").strip().lower()


def get_agent_backend_name() -> str:
    raw = os.getenv("SOUNIO_STROKE_AGENT_BACKEND", "").strip().lower()
    return raw or get_job_backend_name()


def get_start_embedded_benchmark_worker(default: bool = False) -> bool:
    return _env_flag("SOUNIO_STROKE_START_EMBEDDED_BENCHMARK_WORKER", default=default)


def get_auth_settings() -> AuthSettings:
    raw_tokens = os.getenv("SOUNIO_STROKE_API_TOKENS", "").strip()
    single = os.getenv("SOUNIO_STROKE_API_TOKEN", "").strip()
    token_items: list[str] = []
    if raw_tokens:
        token_items.extend(item.strip() for item in raw_tokens.split(","))
    if single:
        token_items.append(single)
    tokens = tuple(item for item in token_items if item)
    return AuthSettings(tokens=tokens)


def get_minio_settings() -> MinioSettings | None:
    endpoint = os.getenv("SOUNIO_STROKE_MINIO_ENDPOINT", "").strip()
    if not endpoint:
        return None
    access_key = os.getenv("SOUNIO_STROKE_MINIO_ACCESS_KEY", "").strip()
    secret_key = os.getenv("SOUNIO_STROKE_MINIO_SECRET_KEY", "").strip()
    bucket = os.getenv("SOUNIO_STROKE_MINIO_BUCKET", "sounio-stroke-lab").strip()
    parsed = urlparse(endpoint if "://" in endpoint else f"http://{endpoint}")
    secure = parsed.scheme == "https"
    normalized_endpoint = parsed.netloc or parsed.path
    return MinioSettings(
        endpoint=normalized_endpoint,
        access_key=access_key,
        secret_key=secret_key,
        bucket=bucket,
        secure=secure,
    )


def get_kubernetes_settings() -> KubernetesSettings:
    raw_shared_prefixes = os.getenv("SOUNIO_STROKE_K8S_SHARED_PATH_PREFIXES", "/datasets").strip()
    shared_prefixes = tuple(item.strip() for item in raw_shared_prefixes.split(",") if item.strip()) or ("/datasets",)
    return KubernetesSettings(
        namespace=os.getenv("SOUNIO_STROKE_K8S_NAMESPACE", "darwin-genomics").strip() or "darwin-genomics",
        local_queue=os.getenv("SOUNIO_STROKE_K8S_LOCAL_QUEUE", "darwin-lab").strip() or "darwin-lab",
        worker_image=(
            os.getenv(
                "SOUNIO_STROKE_K8S_WORKER_IMAGE",
                "ghcr.io/agourakis82/sounio-stroke-lab-worker:latest",
            ).strip()
            or "ghcr.io/agourakis82/sounio-stroke-lab-worker:latest"
        ),
        dataset_pvc=os.getenv("SOUNIO_STROKE_K8S_DATASET_PVC", "sounio-stroke-datasets").strip() or "sounio-stroke-datasets",
        db_secret_name=os.getenv("SOUNIO_STROKE_K8S_DB_SECRET_NAME", "sounio-stroke-db").strip() or "sounio-stroke-db",
        db_secret_key=os.getenv("SOUNIO_STROKE_K8S_DB_SECRET_KEY", "dsn").strip() or "dsn",
        minio_secret_name=os.getenv("SOUNIO_STROKE_K8S_MINIO_SECRET_NAME", "sounio-stroke-minio").strip() or "sounio-stroke-minio",
        minio_access_key_key=os.getenv("SOUNIO_STROKE_K8S_MINIO_ACCESS_KEY_KEY", "access_key").strip() or "access_key",
        minio_secret_key_key=os.getenv("SOUNIO_STROKE_K8S_MINIO_SECRET_KEY_KEY", "secret_key").strip() or "secret_key",
        service_account_name=(
            os.getenv("SOUNIO_STROKE_K8S_SERVICE_ACCOUNT", "sounio-stroke-runner").strip()
            or "sounio-stroke-runner"
        ),
        cpu_request=os.getenv("SOUNIO_STROKE_K8S_CPU_REQUEST", "1000m").strip() or "1000m",
        memory_request=os.getenv("SOUNIO_STROKE_K8S_MEMORY_REQUEST", "1Gi").strip() or "1Gi",
        cpu_limit=os.getenv("SOUNIO_STROKE_K8S_CPU_LIMIT", "2000m").strip() or "2000m",
        memory_limit=os.getenv("SOUNIO_STROKE_K8S_MEMORY_LIMIT", "2Gi").strip() or "2Gi",
        shared_path_prefixes=shared_prefixes,
    )


def get_runtime_config(storage_root: Path | None = None, *, start_embedded_benchmark_worker: bool | None = None) -> RuntimeConfig:
    return RuntimeConfig(
        storage_root=storage_root or get_storage_root(),
        db_url=get_db_url(),
        object_store=get_object_store_backend(),
        job_backend=get_job_backend_name(),
        agent_backend=get_agent_backend_name(),
        minio=get_minio_settings(),
        auth=get_auth_settings(),
        start_embedded_benchmark_worker=(
            get_start_embedded_benchmark_worker(default=False)
            if start_embedded_benchmark_worker is None
            else start_embedded_benchmark_worker
        ),
    )
