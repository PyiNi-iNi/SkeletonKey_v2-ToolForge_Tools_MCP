"""Secret Management for SkeletonKey Integrations (P5.2).

This module provides secure storage and retrieval of secrets for integrations,
including API keys, tokens, passwords, and other sensitive configuration.

Features:
- Encrypted storage of secrets at rest
- Environment variable fallback
- Integration-specific secret scopes
- Secure retrieval with audit logging
- Secret rotation support
- Masking for display/logging
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets as secrets_module
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SecretInfo:
    """Information about a stored secret."""
    key: str
    integration: str | None = None
    description: str = ""
    created_at: str = ""
    updated_at: str = ""
    expires_at: str | None = None
    rotation_policy: str | None = None
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "integration": self.integration,
            "description": self.description,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
            "rotation_policy": self.rotation_policy,
        }


@dataclass
class SecretValue:
    """A secret value with metadata."""
    value: str
    integration: str | None = None
    source: str = "store"  # store, env, vault, prompt
    masked: bool = True
    
    def __repr__(self) -> str:
        if self.masked:
            return f"SecretValue(<redacted>, integration={self.integration}, source={self.source})"
        return f"SecretValue({self.value!r}, integration={self.integration}, source={self.source})"
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "value": "<redacted>" if self.masked else self.value,
            "integration": self.integration,
            "source": self.source,
        }


class SecretStore:
    """Secure storage for integration secrets.
    
    Secrets are stored encrypted at rest using a master key. The master key
    can be provided via environment variable or derived from a passphrase.
    
    Usage:
        store = SecretStore(path=".sk/secrets.json")
        
        # Set a secret
        store.set("my-api-key", "sk-12345678", integration="my-integration")
        
        # Get a secret
        value = store.get("my-api-key", integration="my-integration")
        
        # List secrets for an integration
        secrets = store.list_secrets(integration="my-integration")
        
        # Delete a secret
        store.delete("my-api-key", integration="my-integration")
    """
    
    def __init__(
        self,
        path: str | Path | None = None,
        master_key: str | None = None,
        encryption: str = "aes-256-gcm",
    ):
        """Initialize the secret store.
        
        Args:
            path: Path to the secrets file
            master_key: Master encryption key (32 bytes for AES-256)
            encryption: Encryption algorithm (aes-256-gcm, aes-256-cbc, plain)
        """
        self.path = Path(path) if path else None
        self.encryption = encryption
        self._secrets: dict[str, dict[str, Any]] = {}
        self._loaded = False
        self._plaintext_warned = False
        
        # Get master key from environment or generate
        self._master_key = master_key or self._get_master_key()
        
        # Load secrets if path is provided
        if self.path and self.path.exists():
            self._load()
    
    def _get_master_key(self) -> str:
        """Get or generate the master encryption key."""
        # Try environment variable first
        env_key = os.environ.get("SKELETONKEY_SECRETS_KEY")
        if env_key:
            return env_key
        
        # Try to read from key file
        key_file = os.environ.get("SKELETONKEY_SECRETS_KEY_FILE")
        if key_file and os.path.exists(key_file):
            with open(key_file) as f:
                return f.read().strip()
        
        # No key available: fall back to plaintext storage. The warning is
        # emitted lazily at *persist* time (see _warn_plaintext_once), never at
        # construction — constructing a store must not write to stderr, because
        # the MCP stdio server builds one on every startup and stderr is part of
        # its diagnostics contract.
        if self.encryption != "plain":
            self.encryption = "plain"
        
        # Generate a random key (for testing only)
        return secrets_module.token_hex(32)
    
    def _derive_key(self, salt: bytes) -> bytes:
        """Derive an encryption key from the master key and salt."""
        kdf = hashlib.pbkdf2_hmac(
            "sha256",
            self._master_key.encode(),
            salt,
            100000,
        )
        return kdf

    def _warn_plaintext_once(self) -> None:
        """Warn once (via logging) that secrets are being stored unencrypted.

        Deferred to persist time on purpose: the store is constructed on every
        toolkit build, and a startup-time `warnings.warn` leaked onto the MCP
        stdio server's stderr channel. The warning belongs at the moment a secret
        is actually about to be written in clear text.
        """
        if self._plaintext_warned:
            return
        self._plaintext_warned = True
        logger.warning(
            "No SKELETONKEY_SECRETS_KEY or SKELETONKEY_SECRETS_KEY_FILE provided. "
            "Secrets will be stored in plain text. Set one of these environment "
            "variables for encryption."
        )
    
    def _encrypt(self, plaintext: str) -> dict[str, Any]:
        """Encrypt a plaintext value."""
        if self.encryption == "plain":
            return {"value": plaintext, "encryption": "plain"}
        
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            
            # Generate random nonce
            nonce = secrets_module.token_bytes(12)
            
            # Derive key
            salt = b"skeletonkey-secrets-salt"
            key = self._derive_key(salt)[:32]
            
            # Encrypt
            aesgcm = AESGCM(key)
            ciphertext = aesgcm.encrypt(nonce, plaintext.encode(), None)
            
            return {
                "value": base64.b64encode(ciphertext).decode(),
                "nonce": base64.b64encode(nonce).decode(),
                "encryption": self.encryption,
                "salt": base64.b64encode(salt).decode(),
            }
        except ImportError:
            # cryptography not installed, fall back to plain
            logger.warning(
                "cryptography package not installed. Secrets will be stored in plain text. "
                "Install with: pip install cryptography"
            )
            self.encryption = "plain"
            return {"value": plaintext, "encryption": "plain"}
    
    def _decrypt(self, encrypted: dict[str, Any]) -> str:
        """Decrypt an encrypted value."""
        if encrypted.get("encryption") == "plain":
            return encrypted["value"]
        
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            
            # Get key
            salt = base64.b64decode(encrypted.get("salt", b"").encode())
            key = self._derive_key(salt)[:32]
            
            # Decrypt
            aesgcm = AESGCM(key)
            nonce = base64.b64decode(encrypted["nonce"].encode())
            ciphertext = base64.b64decode(encrypted["value"].encode())
            
            plaintext = aesgcm.decrypt(nonce, ciphertext, None)
            return plaintext.decode()
        except ImportError:
            # cryptography not installed
            logger.warning("cryptography package not installed. Cannot decrypt secrets.")
            return encrypted["value"]
        except Exception as e:
            logger.error(f"Failed to decrypt secret: {e}")
            return ""
    
    def _load(self) -> None:
        """Load secrets from the storage file."""
        if not self.path:
            return
        
        try:
            with open(self.path) as f:
                data = json.load(f)
                self._secrets = data.get("secrets", {})
            self._loaded = True
            logger.info(f"Loaded {len(self._secrets)} secret entries from {self.path}")
        except FileNotFoundError:
            self._secrets = {}
            self._loaded = False
        except Exception as e:
            logger.error(f"Failed to load secrets from {self.path}: {e}")
            self._secrets = {}
            self._loaded = False
    
    def _save(self) -> None:
        """Save secrets to the storage file."""
        if not self.path:
            return
        
        try:
            # Ensure directory exists
            self.path.parent.mkdir(parents=True, exist_ok=True)
            
            # Write with restricted permissions
            with open(self.path, "w") as f:
                json.dump({"secrets": self._secrets}, f, indent=2)
            
            # Set file permissions to 600 (owner read/write only)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
            
            logger.info(f"Saved {len(self._secrets)} secret entries to {self.path}")
        except Exception as e:
            logger.error(f"Failed to save secrets to {self.path}: {e}")
    
    def _make_key(self, key: str, integration: str | None = None) -> str:
        """Create a unique storage key for a secret."""
        if integration:
            return f"{integration}:{key}"
        return key
    
    def set(
        self,
        key: str,
        value: str,
        integration: str | None = None,
        description: str = "",
        overwrite: bool = False,
    ) -> bool:
        """Set a secret value.
        
        Args:
            key: Secret key/name
            value: Secret value
            integration: Integration name (for scoping)
            description: Description of the secret
            overwrite: Whether to overwrite existing value
            
        Returns:
            bool: True if set successfully
        """
        storage_key = self._make_key(key, integration)
        
        if not overwrite and storage_key in self._secrets:
            logger.warning(f"Secret {storage_key} already exists and overwrite=False")
            return False
        
        # Warn (once) if we're about to persist in plain text.
        if self.encryption == "plain":
            self._warn_plaintext_once()
        
        # Encrypt the value
        encrypted = self._encrypt(value)
        
        # Store with metadata
        self._secrets[storage_key] = {
            "key": key,
            "integration": integration,
            "description": description,
            "value": encrypted,
            "created_at": self._now(),
            "updated_at": self._now(),
        }
        
        self._save()
        logger.info(f"Set secret {storage_key}")
        return True
    
    def get(
        self,
        key: str,
        integration: str | None = None,
        default: str | None = None,
    ) -> SecretValue | None:
        """Get a secret value.
        
        This method checks in order:
        1. Stored secrets
        2. Environment variables (SKELETONKEY_SECRET_<KEY>)
        3. Integration-specific environment variables
        
        Args:
            key: Secret key/name
            integration: Integration name (for scoping)
            default: Default value if not found
            
        Returns:
            SecretValue: The secret value with metadata, or None if not found
        """
        storage_key = self._make_key(key, integration)
        
        # Try stored secrets first
        if storage_key in self._secrets:
            entry = self._secrets[storage_key]
            encrypted = entry.get("value", {})
            value = self._decrypt(encrypted) if isinstance(encrypted, dict) else encrypted
            return SecretValue(
                value=value,
                integration=integration,
                source="store",
                masked=False,
            )
        
        # Try environment variables
        env_key = f"SKELETONKEY_SECRET_{key.upper()}"
        if env_key in os.environ:
            return SecretValue(
                value=os.environ[env_key],
                integration=integration,
                source="env",
                masked=False,
            )
        
        # Try integration-specific environment variable
        if integration:
            env_key = f"SKELETONKEY_SECRET_{integration.upper()}_{key.upper()}"
            if env_key in os.environ:
                return SecretValue(
                    value=os.environ[env_key],
                    integration=integration,
                    source="env",
                    masked=False,
                )
        
        # Not found
        if default is not None:
            return SecretValue(
                value=default,
                integration=integration,
                source="default",
                masked=False,
            )
        
        return None
    
    def get_or_prompt(
        self,
        key: str,
        integration: str | None = None,
        prompt: str = "",
    ) -> SecretValue | None:
        """Get a secret, prompting the user if not found.
        
        Args:
            key: Secret key/name
            integration: Integration name (for scoping)
            prompt: Prompt message for the user
            
        Returns:
            SecretValue: The secret value, or None if user cancelled
        """
        result = self.get(key, integration)
        if result:
            return result
        
        # Prompt user
        if prompt:
            print(prompt, end=" ")
        else:
            print(f"Enter value for secret '{key}'{f' ({integration})' if integration else ''}: ", end="")
        
        try:
            import getpass
            value = getpass.getpass("")
        except ImportError:
            value = input("")
        
        if not value:
            return None
        
        # Store the value
        self.set(key, value, integration=integration)
        
        return SecretValue(
            value=value,
            integration=integration,
            source="prompt",
            masked=False,
        )
    
    def delete(self, key: str, integration: str | None = None) -> bool:
        """Delete a secret.
        
        Args:
            key: Secret key/name
            integration: Integration name (for scoping)
            
        Returns:
            bool: True if deleted successfully
        """
        storage_key = self._make_key(key, integration)
        
        if storage_key not in self._secrets:
            return False
        
        del self._secrets[storage_key]
        self._save()
        logger.info(f"Deleted secret {storage_key}")
        return True
    
    def list_secrets(self, integration: str | None = None) -> list[SecretInfo]:
        """List secret information (values are redacted).
        
        Args:
            integration: Integration name to filter by (None = all)
            
        Returns:
            list: List of SecretInfo objects
        """
        result = []
        for storage_key, entry in self._secrets.items():
            if integration and entry.get("integration") != integration:
                continue
            result.append(SecretInfo(
                key=entry.get("key", storage_key),
                integration=entry.get("integration"),
                description=entry.get("description", ""),
                created_at=entry.get("created_at", ""),
                updated_at=entry.get("updated_at", ""),
            ))
        return result
    
    def has(self, key: str, integration: str | None = None) -> bool:
        """Check if a secret exists.
        
        Args:
            key: Secret key/name
            integration: Integration name (for scoping)
            
        Returns:
            bool: True if the secret exists
        """
        storage_key = self._make_key(key, integration)
        return storage_key in self._secrets
    
    def _now(self) -> str:
        """Get current timestamp."""
        from datetime import datetime
        return datetime.utcnow().isoformat() + "Z"
    
    def rotate(
        self,
        key: str,
        new_value: str,
        integration: str | None = None,
    ) -> bool:
        """Rotate a secret value (update with new value).
        
        Args:
            key: Secret key/name
            new_value: New secret value
            integration: Integration name (for scoping)
            
        Returns:
            bool: True if rotated successfully
        """
        storage_key = self._make_key(key, integration)
        
        if storage_key not in self._secrets:
            return False
        
        # Warn (once) if we're about to persist in plain text.
        if self.encryption == "plain":
            self._warn_plaintext_once()
        
        # Encrypt the new value
        encrypted = self._encrypt(new_value)
        
        # Update
        self._secrets[storage_key]["value"] = encrypted
        self._secrets[storage_key]["updated_at"] = self._now()
        
        self._save()
        logger.info(f"Rotated secret {storage_key}")
        return True
    
    def import_secrets(self, data: dict[str, Any], integration: str | None = None) -> int:
        """Import multiple secrets from a dictionary.
        
        Args:
            data: Dictionary of key-value pairs
            integration: Integration name (for scoping)
            
        Returns:
            int: Number of secrets imported
        """
        count = 0
        for key, value in data.items():
            if self.set(key, value, integration=integration, overwrite=True):
                count += 1
        return count
    
    def export_secrets(self, integration: str | None = None) -> dict[str, str]:
        """Export secrets as a dictionary.
        
        WARNING: This returns plaintext values! Use with caution.
        
        Args:
            integration: Integration name to filter by (None = all)
            
        Returns:
            dict: Dictionary of key-value pairs
        """
        result = {}
        for storage_key, entry in self._secrets.items():
            if integration and entry.get("integration") != integration:
                continue
            encrypted = entry.get("value", {})
            value = self._decrypt(encrypted) if isinstance(encrypted, dict) else encrypted
            result[entry.get("key", storage_key)] = value
        return result
    
    def clear(self, integration: str | None = None) -> int:
        """Clear all secrets (optionally for a specific integration).
        
        Args:
            integration: Integration name to clear (None = all)
            
        Returns:
            int: Number of secrets cleared
        """
        if integration:
            # Clear only secrets for this integration
            to_delete = [
                key for key, entry in self._secrets.items()
                if entry.get("integration") == integration
            ]
            for key in to_delete:
                del self._secrets[key]
            count = len(to_delete)
        else:
            # Clear all secrets
            count = len(self._secrets)
            self._secrets = {}
        
        self._save()
        logger.info(f"Cleared {count} secrets")
        return count


# Global secret store instance (initialized lazily)
_secret_store: SecretStore | None = None


def get_secret_store(path: str | Path | None = None) -> SecretStore:
    """Get the global secret store instance.
    
    Args:
        path: Path to the secrets file (optional, uses default if not provided)
        
    Returns:
        SecretStore: The global secret store
    """
    global _secret_store
    
    if _secret_store is None:
        # Try to get path from config
        if path is None:
            try:
                from .config import Config
                cfg = Config.load()
                integrations_config = getattr(cfg, 'integrations', None)
                if integrations_config:
                    secrets_dir = integrations_config.get('secrets_dir', '')
                    if secrets_dir:
                        path = Path(secrets_dir) / "secrets.json"
                    else:
                        path = Path(cfg.state.dir) / "integrations" / "secrets.json"
                else:
                    path = None
            except Exception:
                path = None
        
        _secret_store = SecretStore(path=path)
    
    return _secret_store
