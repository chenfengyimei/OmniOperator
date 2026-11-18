"""Desktop transport public entry; platform probing only occurs through explicit calls."""

from .backend import DesktopBackend

__all__ = ["DesktopBackend"]
