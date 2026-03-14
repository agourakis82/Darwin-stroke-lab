from __future__ import annotations

import json
import mimetypes
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from sounio_stroke_lab.config import MinioSettings

try:
    from minio import Minio
except ImportError:  # pragma: no cover - optional dependency path
    Minio = None


@dataclass(frozen=True)
class StoredObject:
    local_path: Path
    uri: str | None = None
    bytes: int | None = None
    content_type: str | None = None


class ObjectStore(ABC):
    def __init__(self, cache_root: Path):
        self.cache_root = cache_root

    def initialize(self) -> None:
        self.cache_root.mkdir(parents=True, exist_ok=True)

    @abstractmethod
    def write_text(self, relative_path: str, content: str) -> StoredObject:
        raise NotImplementedError

    @abstractmethod
    def append_text(self, relative_path: str, content: str) -> StoredObject:
        raise NotImplementedError

    @abstractmethod
    def write_json(self, relative_path: str, payload: dict) -> StoredObject:
        raise NotImplementedError

    @abstractmethod
    def ensure_object(self, relative_path: str, source_path: Path) -> StoredObject:
        raise NotImplementedError


class LocalObjectStore(ObjectStore):
    def _target(self, relative_path: str) -> Path:
        return self.cache_root / relative_path

    def write_text(self, relative_path: str, content: str) -> StoredObject:
        path = self._target(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return StoredObject(
            local_path=path,
            bytes=path.stat().st_size,
            content_type=mimetypes.guess_type(str(path))[0] or "text/plain",
        )

    def append_text(self, relative_path: str, content: str) -> StoredObject:
        path = self._target(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(content)
        return StoredObject(
            local_path=path,
            bytes=path.stat().st_size,
            content_type=mimetypes.guess_type(str(path))[0] or "text/plain",
        )

    def write_json(self, relative_path: str, payload: dict) -> StoredObject:
        return self.write_text(relative_path, json.dumps(payload, indent=2))

    def ensure_object(self, relative_path: str, source_path: Path) -> StoredObject:
        target = self._target(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if source_path.resolve() != target.resolve():
            shutil.copy2(source_path, target)
        return StoredObject(
            local_path=target,
            bytes=target.stat().st_size if target.exists() else None,
            content_type=mimetypes.guess_type(str(target))[0] or "application/octet-stream",
        )


class MinioObjectStore(ObjectStore):
    def __init__(
        self,
        cache_root: Path,
        settings: MinioSettings,
        *,
        client_factory=None,
    ):
        super().__init__(cache_root)
        self.settings = settings
        self._client_factory = client_factory
        self._client = None

    @property
    def client(self):
        if self._client is None:
            if self._client_factory is not None:
                self._client = self._client_factory()
            else:
                if Minio is None:  # pragma: no cover - dependency availability
                    raise RuntimeError(
                        "MinIO object store requested but the 'minio' package is not installed."
                    )
                self._client = Minio(
                    self.settings.endpoint,
                    access_key=self.settings.access_key,
                    secret_key=self.settings.secret_key,
                    secure=self.settings.secure,
                )
        return self._client

    def initialize(self) -> None:
        super().initialize()
        if not self.client.bucket_exists(self.settings.bucket):
            self.client.make_bucket(self.settings.bucket)

    def _key(self, relative_path: str) -> str:
        return relative_path.replace("\\", "/").lstrip("/")

    def _uri(self, key: str) -> str:
        return f"minio://{self.settings.bucket}/{key}"

    def _upload(self, path: Path, relative_path: str) -> StoredObject:
        key = self._key(relative_path)
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.client.fput_object(
            self.settings.bucket,
            key,
            str(path),
            content_type=content_type,
        )
        return StoredObject(
            local_path=path,
            uri=self._uri(key),
            bytes=path.stat().st_size if path.exists() else None,
            content_type=content_type,
        )

    def write_text(self, relative_path: str, content: str) -> StoredObject:
        path = self.cache_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return self._upload(path, relative_path)

    def append_text(self, relative_path: str, content: str) -> StoredObject:
        path = self.cache_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(content)
        return self._upload(path, relative_path)

    def write_json(self, relative_path: str, payload: dict) -> StoredObject:
        return self.write_text(relative_path, json.dumps(payload, indent=2))

    def ensure_object(self, relative_path: str, source_path: Path) -> StoredObject:
        target = self.cache_root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if source_path.resolve() != target.resolve():
            shutil.copy2(source_path, target)
        return self._upload(target, relative_path)
