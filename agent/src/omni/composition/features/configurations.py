"""Register model and routing configuration views over the instance database."""

from ...application.configuration import ConfigurationService, ConfigurationServices
from ...application.routing import RoutingService
from . import FeatureBinding, FeatureContext


async def install_configurations(context: FeatureContext) -> FeatureBinding:
    configurations = ConfigurationService(context.database, context.token_counter)
    return FeatureBinding(ConfigurationServices(configurations, RoutingService(context.database)))
