"""Declare bundled feature installers and their authenticated routers."""

from ...adapters.http_routes.action_reviews import action_reviews_router
from ...adapters.http_routes.android import android_router
from ...adapters.http_routes.bilibili import bilibili_router
from ...adapters.http_routes.configurations import configurations_router
from ...adapters.http_routes.credentials import credentials_router
from ...adapters.http_routes.deliveries import deliveries_router
from ...adapters.http_routes.desktop import desktop_router
from ...adapters.http_routes.documents import documents_router
from ...adapters.http_routes.extensions import extensions_router
from ...adapters.http_routes.learning import learning_router
from ...adapters.http_routes.maintenance import maintenance_router
from ...adapters.http_routes.mcp import mcp_router
from ...adapters.http_routes.media import media_router
from ...adapters.http_routes.memory import memory_router
from ...adapters.http_routes.operations import operations_router
from ...adapters.http_routes.people import people_router
from ...adapters.http_routes.qq import qq_router
from ...adapters.http_routes.schedules import schedules_router
from ...adapters.http_routes.skills import skills_router
from ...adapters.http_routes.terminals import terminals_router
from ...adapters.http_routes.vision import vision_router
from . import FeaturePlugin
from .action_reviews import install_action_reviews
from .android import install_android
from .bilibili import install_bilibili
from .configurations import install_configurations
from .credentials import install_credentials
from .deliveries import install_deliveries
from .desktop import install_desktop
from .documents import install_documents
from .extensions import install_extensions
from .learning import install_learning
from .maintenance import install_maintenance
from .mcp import install_mcp
from .media import install_media
from .memory import install_memory
from .omniweave import install_omniweave
from .operations import install_operations
from .people import install_people
from .qq import install_qq
from .schedules import install_schedules
from .skills import install_skills
from .terminals import install_terminals
from .vision import install_vision

BUILTIN_FEATURES = (
    FeaturePlugin(
        "configurations",
        factory=configurations_router,
        install=install_configurations,
        extra_paths=(
            "/agents/{agent_id}/routing",
            "/agents/{agent_id}/runs/{run_id}/routing",
            "/agents/{agent_id}/settings",
            "/agents/{agent_id}/settings/revisions",
            "/agents/{agent_id}/runs/{run_id}/configuration",
        ),
    ),
    FeaturePlugin(
        "people",
        factory=people_router,
        install=install_people,
        extra_paths=(
            "/agents/{agent_id}/memory/{memory_id}/revisions",
            "/agents/{agent_id}/persona-growth",
            "/agents/{agent_id}/persona-growth/rebuild",
        ),
    ),
    FeaturePlugin("documents", factory=documents_router, install=install_documents),
    FeaturePlugin("memory", factory=memory_router, install=install_memory),
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
    FeaturePlugin("credentials", factory=credentials_router, install=install_credentials, instance_scoped=True),
)

BUILTIN_WORKFLOW_FEATURES = (
    FeaturePlugin(
        "vision",
        factory=vision_router,
        install=install_vision,
        extra_paths=(
            "/agents/{agent_id}/images",
            "/agents/{agent_id}/images/{object_id}/prepare",
            "/agents/{agent_id}/runs/{run_id}/images",
        ),
    ),
    FeaturePlugin(
        "learning",
        factory=learning_router,
        install=install_learning,
        extra_paths=(
            "/agents/{agent_id}/reflections",
            "/agents/{agent_id}/reflections/{journal_id}",
            "/agents/{agent_id}/reflections/candidates/{candidate_id}/decision",
            "/agents/{agent_id}/memory/reviews",
            "/agents/{agent_id}/memory/reviews/{batch_id}",
            "/agents/{agent_id}/memory/reviews/{batch_id}/cancel",
            "/agents/{agent_id}/memory/reviews/candidates/{candidate_id}/decision",
        ),
    ),
    FeaturePlugin("maintenance", factory=maintenance_router, install=install_maintenance),
)

BUILTIN_KERNEL_FEATURES = (
    FeaturePlugin("omniweave", factory=None, install=install_omniweave),
    FeaturePlugin("schedules", factory=schedules_router, install=install_schedules),
    FeaturePlugin("qq", factory=qq_router, install=install_qq),
    FeaturePlugin(
        "action-reviews",
        factory=action_reviews_router,
        install=install_action_reviews,
        extra_paths=("/agents/{agent_id}/actions/{action_id}/review", "/agents/{agent_id}/actions/{action_id}/reviews"),
    ),
    FeaturePlugin(
        "system",
        factory=operations_router,
        install=install_operations,
        instance_scoped=True,
        extra_paths=(
            "/agents/{agent_id}/audit",
            "/agents/{agent_id}/debug",
            "/agents/{agent_id}/debug/{operation_id}",
            "/agents/{agent_id}/debug/{operation_id}/logs",
            "/agents/{agent_id}/events/retention-preview",
            "/agents/{agent_id}/audit/retention-preview",
            "/agents/{agent_id}/runs/{run_id}/retention",
            "/agents/{agent_id}/runs/{run_id}/retention-pin",
        ),
    ),
)
