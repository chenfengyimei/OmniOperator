"""Shared inventory of executable V2 resources for runtime and deployment identities."""

LAYERS = ("domain", "application", "adapters", "composition")
CODE_SUFFIXES = frozenset({".py", ".ps1", ".cs", ".swift", ".c", ".h", ".java", ".xml"})
CODE_CACHE_DIRS = frozenset({"__pycache__", ".build", ".swiftpm"})
