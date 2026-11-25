"""Register owner credential changes with the delivery revocation callback."""

from ...application.credentials import CredentialService
from ...application.delivery import DeliveryService
from . import FeatureBinding, FeatureContext, require_service


async def install_credentials(context: FeatureContext) -> FeatureBinding:
    deliveries = require_service(context.services, "deliveries", DeliveryService)
    service = CredentialService(
        context.database, context.secrets, context.ids, deliveries.cancel_active, context.observer
    )
    context.lifetime.own("credential_changes", 0, service.close, service.stop_accepting)
    return FeatureBinding(service)
