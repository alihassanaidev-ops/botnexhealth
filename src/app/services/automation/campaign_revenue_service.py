"""Revenue attributed to a campaign (Task 12).

Client-first and outcome-based: a campaign is credited with the confirmations
and bookings it recorded itself, valued at the clinic's average visit value.

- Confirmations and reminders protect revenue (averted no-shows).
- Recall and sales bookings generate revenue.

"Protected" and "generated" are reported separately and never summed into one
headline, because a single figure that adds saved money to earned money looks
inflated and is the fastest way to lose a clinic owner's trust. The rule is
returned with the figure: a revenue number shown without its rule starts
arguments instead of settling them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

#: Categories whose value is averted no-shows (a confirmation), not new revenue.
PROTECTED_CATEGORIES = frozenset({"appointment_confirmation", "appointment_ops"})
#: Categories whose value is a booking the campaign produced.
GENERATED_CATEGORIES = frozenset({"recall", "sales", "treatment", "reactivation"})

ATTRIBUTION_RULE = (
    "Credited with the confirmations and bookings the campaign recorded itself, "
    "valued at your average visit value. Confirmations and reminders count as "
    "revenue protected; recall and sales bookings count as revenue generated."
)


@dataclass(frozen=True)
class CampaignRevenue:
    currency: str
    generated: float
    protected: float
    total: float
    spend: float
    roi: float
    #: False when the clinic has not set an average visit value; the UI then
    #: prompts them rather than showing a misleading $0.
    configured: bool
    rule: str
    value_source: str | None


def attribution_for(category: str) -> str:
    """Whether this campaign's value is generated revenue, protected, or neither."""
    normalized = (category or "").lower()
    if normalized in PROTECTED_CATEGORIES:
        return "protected"
    if normalized in GENERATED_CATEGORIES:
        return "generated"
    return "none"


def compute_campaign_revenue(
    *,
    category: str,
    summary: Mapping[str, Any],
    values: Mapping[str, Any] | None,
    value_source: str | None,
    currency: str,
    spend: float,
) -> CampaignRevenue:
    """Attributed revenue for one campaign from its counts and the clinic values."""
    avg = float((values or {}).get("avg_appointment_value") or 0.0)
    booked = int(summary.get("booked") or 0)
    confirmed = int(summary.get("confirmed") or 0)
    kind = attribution_for(category)

    generated = avg * booked if kind == "generated" else 0.0
    protected = avg * confirmed if kind == "protected" else 0.0
    total = generated + protected
    spend_value = float(spend or 0.0)

    return CampaignRevenue(
        currency=currency,
        generated=round(generated, 2),
        protected=round(protected, 2),
        total=round(total, 2),
        spend=round(spend_value, 2),
        roi=round(total - spend_value, 2),
        configured=values is not None,
        rule=ATTRIBUTION_RULE,
        value_source=value_source if values is not None else None,
    )
