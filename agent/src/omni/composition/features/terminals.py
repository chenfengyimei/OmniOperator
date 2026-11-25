"""Bind terminal sessions and their review service to the same platform port."""

from __future__ import annotations

from ...application.terminal_review import TerminalReviewService
from ...application.terminal_services import TerminalServices
from ...application.terminals import TerminalService
from . import FeatureBinding, FeatureContext


async def install_terminals(context: FeatureContext) -> FeatureBinding:
    terminals = TerminalService(context.database, context.platform.terminal, context.clock, context.ids)
    context.lifetime.own("terminals", 0, terminals.close, terminals.stop_accepting)
    reviews = TerminalReviewService(
        context.database,
        context.observer,
        native_quiescent=terminals.review_quiescent,
        while_quiescent=terminals.while_quiescent,
    )
    context.lifetime.own("terminal_reviews", 0, reviews.close, reviews.stop_accepting)
    return FeatureBinding(TerminalServices(terminals, reviews))
