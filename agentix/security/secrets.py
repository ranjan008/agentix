"""
Secrets Vault abstraction — tiered backend support.

Backends:
  env       — read from environment variables (Lite tier default)
  file      — read from an encrypted local file (Lite tier alternative)
  vault     — HashiCorp Vault (Standard / Enterprise)
  aws_sm    — AWS Secrets Manager (Standard / Enterprise)

Usage:
  vault = SecretsVault.from_config(cfg)
  api_key = vault.get("ZENDESK_API_KEY")
  vault.set("MY_SECRET", "value")   # only supported on file/vault/aws_sm

vault:// URI scheme (used in skill specs):
  vault://zendesk/api_key  → secret_path="zendesk/api_key"
"""
from __future__ import annotations

import base64
import json
import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path

logger = logging.getLogger(__name__)


class SecretNotFoundError(Exception):
    pass


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class SecretsBackend(ABC):
    @abstractmethod
    def get(self, key: str) -> str:
        ...

    @abstractmethod
    def set(self, key: str, value: str) -> None:
        ...

    @abstractmethod
    def delete(self, key: str) -> None:
        ...

    @abstractmethod
    def list_keys(self) -> list[str]:
        ...


# ---------------------------------------------------------------------------
# Env backend (Lite tier)
# ---------------------------------------------------------------------------

class EnvBackend(SecretsBackend):
    """Reads secrets from environment variables. Read-only."""

    def get(self, key: str) -> str:
        # Normalise vault:// URIs to env var names
        key = _vault_uri_to_key(key)
        val = os.environ.get(key)
        if val is None:
            raise SecretNotFoundError(f"Secret '{key}' not found in environment")
        return val

    def set(self, key: str, value: str) -> None:
        os.environ[_vault_uri_to_key(key)] = value

    def delete(self, key: str) -> None:
        os.environ.pop(_vault_uri_to_key(key), None)

    def list_keys(self) -> list[str]:
        return list(os.environ.keys())


# ---------------------------------------------------------------------------
# File backend (Lite tier — simple JSON store, base64-obfuscated)
# ---------------------------------------------------------------------------

class FileBackend(SecretsBackend):
    """
    Stores secrets in a local JSON file.
    Values are base64-encoded (obfuscation only — not encryption).
    For real encryption upgrade to Fernet or use HashiCorp Vault.
    """

    def __init__(self, path: str = "data/secrets.json") -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._path.write_text("{}")

    def _load(self) -> dict:
        return json.loads(self._path.read_text())

    def _save(self, data: dict) -> None:
        self._path.write_text(json.dumps(data, indent=2))

    def get(self, key: str) -> str:
        key = _vault_uri_to_key(key)
        data = self._load()
        if key not in data:
            # Fall back to env
            val = os.environ.get(key)
            if val:
                return val
            raise SecretNotFoundError(f"Secret '{key}' not found")
        return base64.b64decode(data[key].encode()).decode()

    def set(self, key: str, value: str) -> None:
        key = _vault_uri_to_key(key)
        data = self._load()
        data[key] = base64.b64encode(value.encode()).decode()
        self._save(data)

    def delete(self, key: str) -> None:
        key = _vault_uri_to_key(key)
        data = self._load()
        data.pop(key, None)
        self._save(data)

    def list_keys(self) -> list[str]:
        return list(self._load().keys())


# ---------------------------------------------------------------------------
# HashiCorp Vault backend (Standard / Enterprise)
# ---------------------------------------------------------------------------

class HashiCorpVaultBackend(SecretsBackend):
    """
    Reads/writes secrets from HashiCorp Vault KV v2.
    Requires: pip install hvac
    """

    def __init__(
        self,
        url: str = "",
        token: str = "",
        mount_point: str = "secret",
        namespace: str = "",
    ) -> None:
        self.url = url or os.environ.get("VAULT_ADDR", "http://127.0.0.1:8200")
        self.token = token or os.environ.get("VAULT_TOKEN", "")
        self.mount_point = mount_point
        self.namespace = namespace
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import hvac
                self._client = hvac.Client(url=self.url, token=self.token, namespace=self.namespace or None)
                if not self._client.is_authenticated():
                    raise RuntimeError("HashiCorp Vault authentication failed")
            except ImportError:
                raise ImportError("HashiCorp Vault backend requires 'hvac': pip install hvac")
        return self._client

    def get(self, key: str) -> str:
        path = _vault_uri_to_path(key)
        client = self._get_client()
        try:
            secret = client.secrets.kv.v2.read_secret_version(
                path=path, mount_point=self.mount_point
            )
            data = secret["data"]["data"]
            # If path has a field (e.g. "zendesk/api_key" → path="zendesk", field="api_key")
            parts = path.rsplit("/", 1)
            field = parts[-1] if len(parts) > 1 else "value"
            return data.get(field) or data.get("value") or next(iter(data.values()))
        except Exception as e:
            raise SecretNotFoundError(f"Vault secret '{key}': {e}")

    def set(self, key: str, value: str) -> None:
        path = _vault_uri_to_path(key)
        parts = path.rsplit("/", 1)
        field = parts[-1] if len(parts) > 1 else "value"
        secret_path = parts[0] if len(parts) > 1 else path
        self._get_client().secrets.kv.v2.create_or_update_secret(
            path=secret_path,
            secret={field: value},
            mount_point=self.mount_point,
        )

    def delete(self, key: str) -> None:
        path = _vault_uri_to_path(key)
        self._get_client().secrets.kv.v2.delete_metadata_and_all_versions(
            path=path, mount_point=self.mount_point
        )

    def list_keys(self) -> list[str]:
        try:
            result = self._get_client().secrets.kv.v2.list_secrets(
                path="", mount_point=self.mount_point
            )
            return result["data"]["keys"]
        except Exception:
            return []


# ---------------------------------------------------------------------------
# AWS Secrets Manager backend (Standard / Enterprise)
# ---------------------------------------------------------------------------

class AWSSecretsManagerBackend(SecretsBackend):
    """
    Reads/writes secrets from AWS Secrets Manager.
    Requires: pip install boto3
    """

    def __init__(self, region: str = "", prefix: str = "agentix/") -> None:
        self.region = region or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
        self.prefix = prefix
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import boto3
                self._client = boto3.client("secretsmanager", region_name=self.region)
            except ImportError:
                raise ImportError("AWS Secrets Manager backend requires 'boto3': pip install boto3")
        return self._client

    def get(self, key: str) -> str:
        name = self.prefix + _vault_uri_to_key(key)
        try:
            resp = self._get_client().get_secret_value(SecretId=name)
            return resp.get("SecretString") or base64.b64decode(resp["SecretBinary"]).decode()
        except Exception as e:
            raise SecretNotFoundError(f"AWS SM secret '{key}': {e}")

    def set(self, key: str, value: str) -> None:
        name = self.prefix + _vault_uri_to_key(key)
        client = self._get_client()
        try:
            client.put_secret_value(SecretId=name, SecretString=value)
        except client.exceptions.ResourceNotFoundException:
            client.create_secret(Name=name, SecretString=value)

    def delete(self, key: str) -> None:
        name = self.prefix + _vault_uri_to_key(key)
        self._get_client().delete_secret(SecretId=name, ForceDeleteWithoutRecovery=True)

    def list_keys(self) -> list[str]:
        paginator = self._get_client().get_paginator("list_secrets")
        keys = []
        for page in paginator.paginate():
            for s in page["SecretList"]:
                keys.append(s["Name"].removeprefix(self.prefix))
        return keys


# ---------------------------------------------------------------------------
# Facade
# ---------------------------------------------------------------------------

class SecretsVault:
    """
    Unified secrets interface. Tries backends in order (primary + fallback to env).
    """

    def __init__(self, backend: SecretsBackend) -> None:
        self._backend = backend
        self._env_fallback = EnvBackend()

    def get(self, key: str) -> str:
        try:
            return self._backend.get(key)
        except SecretNotFoundError:
            return self._env_fallback.get(key)

    def set(self, key: str, value: str) -> None:
        self._backend.set(key, value)

    def delete(self, key: str) -> None:
        self._backend.delete(key)

    def list_keys(self) -> list[str]:
        return self._backend.list_keys()

    @classmethod
    def from_config(cls, cfg: dict) -> "SecretsVault":
        """
        Build a SecretsVault from watchdog config.
        cfg example:
          secrets:
            backend: "file"          # env | file | vault | aws_sm
            path: "data/secrets.json"
            vault_addr: "http://vault:8200"
            vault_token_env: "VAULT_TOKEN"
        """
        backend_type = cfg.get("backend", "env")
        if backend_type == "env":
            return cls(EnvBackend())
        elif backend_type == "file":
            return cls(FileBackend(cfg.get("path", "data/secrets.json")))
        elif backend_type == "vault":
            return cls(HashiCorpVaultBackend(
                url=cfg.get("vault_addr", ""),
                token=os.environ.get(cfg.get("vault_token_env", "VAULT_TOKEN"), ""),
                mount_point=cfg.get("mount_point", "secret"),
                namespace=cfg.get("namespace", ""),
            ))
        elif backend_type == "aws_sm":
            return cls(AWSSecretsManagerBackend(
                region=cfg.get("region", ""),
                prefix=cfg.get("prefix", "agentix/"),
            ))
        else:
            logger.warning("Unknown secrets backend '%s', falling back to env", backend_type)
            return cls(EnvBackend())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _vault_uri_to_key(ref: str) -> str:
    """Convert vault://path/field to UPPER_SNAKE env-key style."""
    if ref.startswith("vault://"):
        ref = ref[len("vault://"):]
    return ref.replace("/", "_").upper()


def _vault_uri_to_path(ref: str) -> str:
    """Strip vault:// prefix, keep the path."""
    if ref.startswith("vault://"):
        return ref[len("vault://"):]
    return ref
