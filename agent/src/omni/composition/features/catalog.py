"""Declare bundled feature installers and their authenticated routers."""

from ...adapters.http_routes.action_reviews import action_reviews_router
from ...adapters.http_routes.android import android_router
from ...adapters.http_routes.bilibili import bilibili_router
from ...adapters.http_routes.deliveries import deliveries_router
from ...adapters.http_routes.desktop import desktop_router
from ...adapters.http_routes.extensions import extensions_router
from ...adapters.http_routes.mcp import mcp_router
from ...adapters.http_routes.media import media_router
from ...adapters.http_routes.qq import qq_router
from ...adapters.http_routes.schedules import schedules_router
from ...adapters.http_routes.skills import skills_router
from ...adapters.http_routes.terminals import terminals_router
from . import FeaturePlugin
from .action_reviews import install_action_reviews
from .android import install_android
from .bilibili import install_bilibili
from .deliveries import install_deliveries
from .desktop import install_desktop
from .extensions import install_extensions
from .mcp import install_mcp
from .media import install_media
from .qq import install_qq
from .schedules import install_schedules
from .skills import install_skills
from .terminals import install_terminals

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
    FeaturePlugin("deliveries", factory=deliveries_router, install=install_deliveries),
    FeaturePlugin(
        "terminals",
        factory=terminals_router,
        install=install_terminals,
        extra_paths=(
            "/agents/{agent_id}/terminal-operations/{operation_id}",
            "/agents/{agent_id}/terminal-reviews",
            "/agents/{agent_id}/terminal-reviews/{operation_id}",
            "/agents/{agent_id}/terminal-reviews/{operation_id}/history",
            "/agents/{agent_id}/terminal-reviews/{operation_id}/history/{review_id}/evidence",
        ),
    ),
)

BUILTIN_KERNEL_FEATURES = (
    FeaturePlugin("schedules", factory=schedules_router, install=install_schedules),
    FeaturePlugin("qq", factory=qq_router, install=install_qq),
    FeaturePlugin(
        "action-reviews",
        factory=action_reviews_router,
        install=install_action_reviews,
        extra_paths=("/agents/{agent_id}/actions/{action_id}/review", "/agents/{agent_id}/actions/{action_id}/reviews"),
    ),
)
