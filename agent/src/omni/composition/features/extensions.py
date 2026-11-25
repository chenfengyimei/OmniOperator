"""Bind owner-installed extensions to their package store and supervised hosts."""

from __future__ import annotations

from ...adapters.extensions.hosts import ExtensionHosts
from ...adapters.extensions.packages import ExtensionPackages
from ...application.capabilities import RunCapabilityProvider
from ...application.extensions import ExtensionService
from ...application.ports import Record
from ...domain.contracts import BusinessError
from . import FeatureBinding, FeatureContext


async def install_extensions(context: FeatureContext) -> FeatureBinding:
    packages = ExtensionPackages(context.home / "cache" / "extensions")
    context.lifetime.own("extension_packages", 1, packages.close)
    hosts = ExtensionHosts(str(context.control / "extension-artifacts"))
    context.lifetime.own("extension_hosts", 0, hosts.close, hosts.stop_accepting)
    service = ExtensionService(context.database, context.objects, packages, hosts)
    context.lifetime.own("extensions", 0, service.close, service.stop_accepting)

    def review_quiescent(record: Record) -> bool:
        proof = record.get("proof")
        source = proof.get("source") if isinstance(proof, dict) else None
        if not isinstance(source, dict) or source.get("kind") != "plugin":
            return False
        epoch = source.get("authority_epoch")
        if type(epoch) is not int:
            raise BusinessError("invalid_revision", "Stored policy revision is invalid")
        return service.review_quiescent(str(record["agent_id"]), str(source["extension_id"]), epoch)

    return FeatureBinding(service, run_provider=RunCapabilityProvider("extension", service, review_quiescent))
