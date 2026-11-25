"""Declare bundled feature installers and their authenticated routers."""

from ...adapters.http_routes.android import android_router
from ...adapters.http_routes.bilibili import bilibili_router
from ...adapters.http_routes.desktop import desktop_router
from ...adapters.http_routes.extensions import extensions_router
from ...adapters.http_routes.mcp import mcp_router
from ...adapters.http_routes.media import media_router
from ...adapters.http_routes.qq import qq_router
from ...adapters.http_routes.schedules import schedules_router
from ...adapters.http_routes.skills import skills_router
from . import FeaturePlugin
from .android import install_android
from .bilibili import install_bilibili
from .desktop import install_desktop
from .extensions import install_extensions
from .mcp import install_mcp
from .media import install_media
from .qq import install_qq
from .schedules import install_schedules
from .skills import install_skills

BUILTIN_FEATURES = (
    FeaturePlugin(
        "skills",
        factory=skills_router,
        install=install_skills,
        extra_paths=("/agents/{agent_id}/runs/{run_id}/skills",),
    ),
    FeaturePlugin("extensions", factory=extensions_router, install=install_extensions),
    FeaturePlugin("mcp", factory=mcp_router, install=install_mcp),
    FeaturePlugin("desktop", factory=desktop_router, install=install_desktop),
    FeaturePlugin("bilibili", factory=bilibili_router, install=install_bilibili),
    FeaturePlugin("media", factory=media_router, install=install_media),
    FeaturePlugin("android", factory=android_router, install=install_android),
)

BUILTIN_KERNEL_FEATURES = (
    FeaturePlugin("schedules", factory=schedules_router, install=install_schedules),
    FeaturePlugin("qq", factory=qq_router, install=install_qq),
)
