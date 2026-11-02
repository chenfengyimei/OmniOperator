"""Explicit OS credential store or externally keyed encrypted vault; no plaintext fallback."""

from __future__ import annotations

import os
import re
from pathlib import Path

import keyring
from cryptography.fernet import Fernet, InvalidToken
from keyring.errors import KeyringError, PasswordDeleteError

from ...domain.contracts import BusinessError


def validate_reference(reference: str) -> None:
    if not re.fullmatch(r"[a-zA-Z0-9_.:/-]{1,200}", reference):
        raise BusinessError("invalid_credential_reference", "Invalid credential reference", status=422)


class SystemSecrets:
    def __init__(self, namespace: str) -> None:
        self.namespace = "OmniOperator:" + namespace

    def get(self, reference: str) -> str | None:
        validate_reference(reference)
        try:
            return keyring.get_password(self.namespace, reference)
        except KeyringError as exc:
            raise BusinessError(
                "credential_store_unavailable", "Unlock the operating-system credential store", status=503
            ) from exc

    def set(self, reference: str, value: str) -> None:
        validate_reference(reference)
        try:
            keyring.set_password(self.namespace, reference, value)
        except KeyringError as exc:
            raise BusinessError(
                "credential_store_unavailable", "Operating-system credential store is unavailable", status=503
            ) from exc

    def delete(self, reference: str) -> None:
        validate_reference(reference)
        try:
            keyring.delete_password(self.namespace, reference)
        except PasswordDeleteError:
            return


class FileVault:
    """Immutable per-reference ciphertext with atomic replacement; master key is external."""

    def __init__(self, directory: Path, key_file: Path) -> None:
        if directory.resolve() == key_file.resolve() or key_file.resolve().is_relative_to(directory.resolve()):
            raise BusinessError("unsafe_master_key", "Vault master key must be outside the vault directory")
        if key_file.is_symlink() or not key_file.is_file():
            raise BusinessError("master_key_missing", "Provide an existing external master-key file")
        if os.name != "nt" and key_file.stat().st_mode & 0o077:
            raise BusinessError("master_key_permissions", "Master key must only be readable by its owner")
        try:
            self.cipher = Fernet(key_file.read_bytes().strip())
        except ValueError as exc:
            raise BusinessError("invalid_master_key", "Master key is not a valid Fernet key") from exc
        self.directory = directory

    def _path(self, reference: str) -> Path:
        import hashlib

        validate_reference(reference)
        if self.directory.is_symlink() or self.directory.is_junction():
            raise BusinessError("unsafe_vault", "Vault directory cannot be a link")
        path = self.directory / (hashlib.sha256(reference.encode()).hexdigest() + ".secret")
        if path.is_symlink():
            raise BusinessError("unsafe_vault", "Vault entry cannot be a link")
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

        path = self._path(reference)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(dir=self.directory, prefix=".secret-")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(self.cipher.encrypt(value.encode()))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)

    def delete(self, reference: str) -> None:
        self._path(reference).unlink(missing_ok=True)
