"""Explicit OS credential store or externally keyed encrypted vault; no plaintext fallback."""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from keyring.errors import KeyringError, PasswordDeleteError

from omni.domain.contracts import BusinessError
from omni.domain.credentials import reference_fingerprint, validate_reference, validate_secret
from omni.adapters.object_store import publish_file, sync_directory
from omni.adapters.platform.runtime.permissions import private_path


def native_keyring():
    # Never select a configured chainer or third-party plaintext backend.
    # Core startup does not call this: permission interaction is user-initiated.
    try:
        if sys.platform == "win32":
            from keyring.backends.Windows import WinVaultKeyring

            return WinVaultKeyring()
        if sys.platform == "darwin":
            from keyring.backends.macOS import Keyring

            return Keyring()
        if sys.platform.startswith("linux"):
            from keyring.backends.SecretService import Keyring

            return Keyring()
    except (ImportError, RuntimeError) as exc:
        raise BusinessError(
            "credential_store_unavailable",
            "Use the native OS credential store or an externally keyed vault",
            status=503,
        ) from exc
    raise BusinessError(
        "credential_backend_untrusted", "No supported OS credential backend; use an externally keyed vault", status=503
    )


class SystemSecrets:
    def __init__(self, namespace: str) -> None:
        self.namespace = "OmniOperator:" + namespace
        self._native: Any = None

    def _backend(self) -> Any:
        if self._native is None:
            self._native = native_keyring()
        return self._native

    def get(self, reference: str) -> str | None:
        validate_reference(reference)
        try:
            value = self._backend().get_password(self.namespace, reference_fingerprint(reference))
            if value is not None and not isinstance(value, str):
                raise BusinessError(
                    "credential_store_unavailable", "Native credential store returned an invalid value", status=503
                )
            return value
        except KeyringError as exc:
            raise BusinessError(
                "credential_store_unavailable", "Unlock the operating-system credential store", status=503
            ) from exc

    def set(self, reference: str, value: str) -> None:
        validate_reference(reference)
        validate_secret(value)
        try:
            self._backend().set_password(self.namespace, reference_fingerprint(reference), value)
        except KeyringError as exc:
            raise BusinessError(
                "credential_store_unavailable", "Operating-system credential store is unavailable", status=503
            ) from exc

    def delete(self, reference: str) -> None:
        validate_reference(reference)
        try:
            self._backend().delete_password(self.namespace, reference_fingerprint(reference))
        except PasswordDeleteError:
            return
        except KeyringError as exc:
            raise BusinessError(
                "credential_store_unavailable", "Operating-system credential store is unavailable", status=503
            ) from exc


class FileVault:
    """Immutable per-reference ciphertext with atomic replacement; master key is external."""

    def __init__(self, directory: Path, key_file: Path) -> None:
        if directory.resolve() == key_file.resolve() or key_file.resolve().is_relative_to(directory.resolve()):
            raise BusinessError("unsafe_master_key", "Vault master key must be outside the vault directory")
        if key_file.is_symlink() or key_file.is_junction() or not key_file.is_file() or key_file.stat().st_size > 4096:
            raise BusinessError("master_key_missing", "Provide an existing external master-key file")
        if os.name != "nt" and key_file.stat().st_mode & 0o077:
            raise BusinessError("master_key_permissions", "Master key must only be readable by its owner")
        try:
            self.cipher = Fernet(key_file.read_bytes().strip())
        except ValueError as exc:
            raise BusinessError("invalid_master_key", "Master key is not a valid Fernet key") from exc
        self.directory = directory

    def _path(self, reference: str) -> Path:
        validate_reference(reference)
        if any(path.is_symlink() or path.is_junction() for path in (self.directory, self.directory.parent)):
            raise BusinessError("unsafe_vault", "Vault directory cannot be a link")
        path = self.directory / (reference_fingerprint(reference) + ".secret")
        if path.is_symlink() or path.is_junction():
            raise BusinessError("unsafe_vault", "Vault entry cannot be a link")
        if path.exists() and (not path.is_file() or path.stat().st_nlink != 1 or path.stat().st_size > 2 * 1024 * 1024):
            raise BusinessError("unsafe_vault", "Vault entry must be a bounded single-link regular file")
        return path

    def get(self, reference: str) -> str | None:
        path = self._path(reference)
        try:
            return self.cipher.decrypt(path.read_bytes()).decode("utf-8")
        except FileNotFoundError:
            return None
        except (InvalidToken, UnicodeError) as exc:
            raise BusinessError(
                "credential_decryption_failed", "Credential cannot be decrypted with this master key"
            ) from exc

    def set(self, reference: str, value: str) -> None:
        import tempfile

        validate_secret(value)
        path = self._path(reference)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        private_path(self.directory, directory=True)
        fd, name = tempfile.mkstemp(dir=self.directory, prefix=".secret-")
        try:
            with os.fdopen(fd, "wb") as stream:
                private_path(Path(name))
                stream.write(self.cipher.encrypt(value.encode()))
                stream.flush()
                os.fsync(stream.fileno())
            self._path(reference)
            publish_file(Path(name), path, replace=True)
        finally:
            Path(name).unlink(missing_ok=True)

    def delete(self, reference: str) -> None:
        path = self._path(reference)
        if not path.exists():
            return
        # Persist removal of the canonical lookup name before acknowledging.
        # A crash may leave encrypted residue, never resurrect that reference.
        retired = self.directory / (".retired-" + str(uuid.uuid4()) + ".secret")
        publish_file(path, retired)
        retired.unlink()
        sync_directory(self.directory)
