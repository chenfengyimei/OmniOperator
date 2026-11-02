"""只在构建时收录单一界面源；不在源码树生成第二份界面。"""

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class WebAssetsHook(BuildHookInterface):
    def initialize(self, version, build_data):
        root = Path(self.root)
        candidates = (root.parent / "web", root / "_web", root / "src/omni/channels/web_static")
        source = next((path for path in candidates if (path / "index.html").is_file()), None)
        if source is None or source.is_symlink() or source.is_junction():
            raise ValueError("完整的界面源码缺失或为链接，拒绝构建不完整发行包")
        target = "_web" if self.target_name == "sdist" else "omni/web_assets"
        for path in source.rglob("*"):
            relative = path.relative_to(source)
            if "node_modules" in relative.parts or path.suffix.lower() not in {
                ".html",
                ".js",
                ".css",
                ".svg",
                ".woff",
                ".woff2",
                ".png",
                ".jpg",
                ".jpeg",
                ".gif",
                ".ico",
                ".webp",
            }:
                continue
            if path.is_symlink() or any(
                p.is_symlink() or p.is_junction() for p in path.parents if p.is_relative_to(source)
            ):
                raise ValueError("界面资源不能包含链接")
            if path.is_file():
                build_data["force_include"][str(path)] = f"{target}/{relative.as_posix()}"
