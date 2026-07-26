"""Alert evaluation.

After each metric collection, the collector calls :func:`evaluate_alerts` with
the fresh snapshot. Any :class:`AlertRule` whose condition matches has its
webhook fired and its ``last_triggered_at`` updated.

Metrics addressed by name (``rule.metric``):
  * ``storage.total`` — repo total bytes
  * ``storage.asset_count`` — asset count
Conditions: ``>``, ``<``, ``==`` (within 1% tolerance for ``==``).
``repo_filter`` is a SQL LIKE pattern (NULL/``%`` matches all repos).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AlertRule
from .notifier import send_webhook

logger = logging.getLogger(__name__)

# Map "metric.path" -> key in the snapshot value dict.
_METRIC_PATHS = {
    "storage.total": "total_bytes",
    "storage.asset_count": "asset_count",
}


def _matches(value: float, condition: str, threshold: float) -> bool:
    if condition == ">":
        return value > threshold
    if condition == "<":
        return value < threshold
    if condition == "==":
        # within 1% tolerance
        if threshold == 0:
            return value == threshold
        return abs(value - threshold) / threshold <= 0.01
    return False


async def evaluate_alerts(session: AsyncSession, snapshot: list[dict]) -> int:
    """Run all enabled rules against ``snapshot``; fire webhooks.

    Returns the number of alerts that fired.
    """
    rules = (await session.execute(select(AlertRule).where(AlertRule.enabled.is_(True)))).scalars().all()
    if not rules:
        return 0

    fired = 0
    for rule in rules:
        field_key = _METRIC_PATHS.get(rule.metric)
        if field_key is None:
            continue
        # Determine which repos this rule applies to.
        pattern = rule.repo_filter or "%"
        for entry in snapshot:
            repo = entry.get("repo", "")
            # Translate SQL LIKE to a simple wildcard check.
            if not _like_match(pattern, repo):
                continue
            value = entry.get(field_key)
            if value is None:
                continue
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue
            if _matches(numeric, rule.condition, rule.threshold):
                await send_webhook(
                    rule.webhook_url,
                    {
                        "rule_id": rule.id,
                        "rule_name": rule.name,
                        "repo": repo,
                        "metric": rule.metric,
                        "value": numeric,
                        "condition": rule.condition,
                        "threshold": rule.threshold,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )
                rule.last_triggered_at = datetime.now(timezone.utc)
                fired += 1
                break  # one fire per rule per evaluation cycle
    if fired:
        await session.commit()
    return fired


def _like_match(pattern: str, value: str) -> bool:
    """Tiny SQL LIKE matcher supporting ``%`` and ``_``."""
    import re
    regex = "^" + re.escape(pattern).replace("%", ".*").replace("_", ".") + "$"
    return re.match(regex, value) is not None
