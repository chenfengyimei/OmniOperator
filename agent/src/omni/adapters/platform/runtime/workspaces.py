"""Workspace admission keeps instance control, secrets and installed code private."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from omni.domain.contracts import BusinessError

from .permissions import private_path


class WorkspacePolicy:
    def __init__(self, home: Path, installed_package: Path) -> None:
        self.home, self.installed_package = home.resolve(), installed_package.resolve()

    def validate(self, value: str) -> None:
        if not value:
            return
        path = Path(value).expanduser()
        if not path.is_absolute() or not path.is_dir() or path.is_symlink() or path.is_junction():
            raise BusinessError("invalid_workspace", "Choose an existing absolute workspace directory", status=422)
        path = path.resolve()
        dedicated = self.home / "workspace"
        if path != dedicated and (path.is_relative_to(self.home) or self.home.is_relative_to(path)):
            raise BusinessError(
                "private_workspace", "Workspace cannot expose instance control or private data", status=422
            )
        if path.is_relative_to(self.installed_package) or self.installed_package.is_relative_to(path):
            raise BusinessError("installed_workspace", "Workspace cannot expose the running installation", status=422)


class WorkerWorkspaces:
    def __init__(self, home: Path) -> None:
        self._root = home.resolve() / "owa-workspaces"

    def prepare(self, run_id: str) -> str:
        try:
            if str(UUID(run_id)) != run_id:
                raise ValueError("Expected a canonical Worker identity")
            folder = self._root / run_id
            for path in (self._root, folder):
                if path.is_symlink() or path.is_junction() or path.resolve() != path:
                    raise OSError("Worker workspace cannot contain links")
                path.mkdir(mode=0o700, exist_ok=True)
                private_path(path, directory=True)
        except (OSError, ValueError):
            raise BusinessError(
                "owa_workspace_unavailable", "The private Worker workspace cannot be prepared"
            ) from None
        return str(folder)
