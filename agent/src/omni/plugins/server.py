"""兼容 python -m omni.plugins.server；实现位于 plugins/community/server.py。"""

from .community.server import create_app, main

__all__ = ["create_app", "main"]

if __name__ == "__main__":
    main()
