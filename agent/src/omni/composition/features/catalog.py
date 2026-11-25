"""Declare bundled feature installers and their authenticated routers."""

from ...adapters.http_routes.android import android_router
from ...adapters.http_routes.media import media_router
from ...adapters.http_routes.schedules import schedules_router
from . import FeaturePlugin
from .android import install_android
from .media import install_media
from .schedules import install_schedules

BUILTIN_FEATURES = (
    FeaturePlugin("media", factory=media_router, install=install_media),
    FeaturePlugin("android", factory=android_router, install=install_android),
)

BUILTIN_KERNEL_FEATURES = (FeaturePlugin("schedules", factory=schedules_router, install=install_schedules),)
