"""One filter language for triggers, conditions, switch cases, and audiences.

Before this, four surfaces each had their own matcher model and their own
editor: the condition node's flat ``{logic, rules}``, each trigger's bespoke
fields (``status_ids``, ``flow_states``, ``tokens``, ``statuses``), and the
audience segment's fixed filter struct. None of them could express a nested
boolean, and the only operators were string equality and membership — so
anything numeric or temporal had to be pre-computed by an LLM or mapper node
before a condition could look at it.

``FilterExpression`` replaces all of that with an arbitrarily nested boolean
tree over a small, typed operator set. The same tree is evaluated here on the
backend and rendered by one component in the builder.

Design notes
------------
* **Coercion is deliberate, not incidental.** Workflow context is assembled from
  webhook payloads, so numbers arrive as ``"1"`` and datetimes as
  ``"2026-07-30T00:00:00"``. Comparison operators coerce both sides and return
  ``False`` when a side cannot be coerced, rather than raising. A filter that
  cannot be evaluated must not abort a run.
* **Missing is not false.** ``is_null`` / ``is_not_null`` / ``is_empty`` are the
  only ways to test presence. Every other operator returns ``False`` on a
  missing field, which keeps "the data never arrived" from silently reading as
  "the value did not match".
* **Time is location-local.** Date-only values are interpreted in the location
  timezone, so "appointment is today" means today at the clinic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dataclass_field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal, Mapping, Union
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = [
    "FilterExpression",
    "FilterGroup",
    "FilterRule",
    "FilterOp",
    "EvaluationContext",
    "evaluate",
    "context_value",
    "path_parts",
    "referenced_fields",
    "FILTER_OPS",
    "OPS_WITHOUT_VALUE",
    "OPS_WITH_LIST_VALUE",
    "OPS_WITH_FIELD_VALUE",
]


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

FilterOp = Literal[
    # equality
    "eq",
    "neq",
    # membership. ``in_case_insensitive`` keeps its original spelling because
    # published definitions already use it.
    "in",
    "not_in",
    "in_case_insensitive",
    "not_in_case_insensitive",
    # text
    "contains",
    "not_contains",
    "starts_with",
    "ends_with",
    "matches",
    # numeric
    "gt",
    "gte",
    "lt",
    "lte",
    "between",
    # temporal
    "before",
    "after",
    "within",
    "older_than",
    # presence
    "is_null",
    "is_not_null",
    "is_empty",
    "is_not_empty",
    # arrays
    "any_of",
    "all_of",
    # field-to-field
    "field_eq",
    "field_neq",
    "field_gt",
    "field_lt",
]

FILTER_OPS: tuple[str, ...] = (
    "eq",
    "neq",
    "in",
    "not_in",
    "in_case_insensitive",
    "not_in_case_insensitive",
    "contains",
    "not_contains",
    "starts_with",
    "ends_with",
    "matches",
    "gt",
    "gte",
    "lt",
    "lte",
    "between",
    "before",
    "after",
    "within",
    "older_than",
    "is_null",
    "is_not_null",
    "is_empty",
    "is_not_empty",
    "any_of",
    "all_of",
    "field_eq",
    "field_neq",
    "field_gt",
    "field_lt",
)

#: Operators that test presence only — a value is rejected.
OPS_WITHOUT_VALUE = frozenset({"is_null", "is_not_null", "is_empty", "is_not_empty"})

#: Operators whose value is a list.
OPS_WITH_LIST_VALUE = frozenset(
    {
        "in",
        "not_in",
        "in_case_insensitive",
        "not_in_case_insensitive",
        "any_of",
        "all_of",
        "between",
    }
)

#: Operators whose value names another context field rather than a literal.
OPS_WITH_FIELD_VALUE = frozenset({"field_eq", "field_neq", "field_gt", "field_lt"})

#: Operators whose value is an ISO-8601 duration.
_OPS_WITH_DURATION_VALUE = frozenset({"within", "older_than"})

# Bounds on `matches`. Workflow authors are internal, so this guards against an
# accidental catastrophic pattern rather than a hostile one.
_MAX_PATTERN_LENGTH = 200
_MAX_MATCH_INPUT_LENGTH = 8192

_ISO_DURATION_RE = re.compile(
    r"^P(?!$)(?:(?P<weeks>\d+)W)?(?:(?P<days>\d+)D)?"
    r"(?:T(?!$)(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)
_RELATIVE_NOW_RE = re.compile(r"^now(?:\s*(?P<sign>[+-])\s*(?P<duration>P.+))?$", re.I)
_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_RuleValue = Union[bool, int, float, str, list[Any], None]


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class FilterRule(BaseModel):
    """A single ``field op value`` test."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["rule"] = "rule"
    field: str = Field(min_length=1, max_length=200)
    op: FilterOp
    value: _RuleValue = None
    # Text operators compare case-insensitively by default, because clinic data
    # is inconsistently cased and an author almost never means otherwise.
    case_sensitive: bool = False

    @field_validator("field")
    @classmethod
    def normalize_field(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("field must not be blank")
        return normalized

    @model_validator(mode="after")
    def check_value_shape(self) -> "FilterRule":
        if self.op in OPS_WITHOUT_VALUE:
            if self.value is not None:
                raise ValueError(f"{self.op} does not take a value")
            return self

        if self.value is None:
            raise ValueError(f"{self.op} requires a value")

        if self.op in OPS_WITH_LIST_VALUE:
            if not isinstance(self.value, list):
                raise ValueError(f"{self.op} requires a list value")
            if not self.value:
                raise ValueError(f"{self.op} requires a non-empty list")
            if self.op == "between" and len(self.value) != 2:
                raise ValueError("between requires exactly two values")
        elif isinstance(self.value, list):
            raise ValueError(f"{self.op} does not take a list value")

        if self.op in OPS_WITH_FIELD_VALUE and not isinstance(self.value, str):
            raise ValueError(f"{self.op} requires a field path as its value")

        if self.op in _OPS_WITH_DURATION_VALUE:
            if not isinstance(self.value, str) or _parse_duration(self.value) is None:
                raise ValueError(
                    f"{self.op} requires an ISO-8601 duration such as P14D or PT6H"
                )

        if self.op == "matches":
            if not isinstance(self.value, str):
                raise ValueError("matches requires a pattern string")
            if len(self.value) > _MAX_PATTERN_LENGTH:
                raise ValueError(
                    f"matches pattern must be at most {_MAX_PATTERN_LENGTH} characters"
                )
            try:
                re.compile(self.value)
            except re.error as exc:
                raise ValueError(f"matches pattern is not a valid regex: {exc}") from exc

        return self


class FilterGroup(BaseModel):
    """A boolean combination of nested expressions.

    ``not`` negates the conjunction of its children, so a single child is the
    common "not this" case and several children read as "not (a and b)".
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["group"] = "group"
    op: Literal["and", "or", "not"] = "and"
    children: list["FilterExpression"] = Field(min_length=1, max_length=50)


FilterExpression = Annotated[
    Union[FilterRule, FilterGroup],
    Field(discriminator="kind"),
]

FilterGroup.model_rebuild()


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvaluationContext:
    """Everything an expression can see while it is evaluated."""

    values: Mapping[str, Any]
    now: datetime = dataclass_field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )
    #: IANA name used to resolve date-only values and naive datetimes.
    timezone_name: str = "UTC"

    @property
    def tzinfo(self) -> Any:
        return _zone(self.timezone_name)


def evaluate(expression: Any, context: EvaluationContext) -> bool:
    """Evaluate an expression. Never raises on data — only on a bad schema."""
    if isinstance(expression, FilterGroup):
        results = (evaluate(child, context) for child in expression.children)
        if expression.op == "and":
            return all(results)
        if expression.op == "or":
            return any(results)
        return not all(results)
    if isinstance(expression, FilterRule):
        return _evaluate_rule(expression, context)
    # An unparsed dict can reach here from a legacy definition; be conservative.
    return False


def _evaluate_rule(rule: FilterRule, context: EvaluationContext) -> bool:
    actual = context_value(context.values, rule.field)

    # Presence operators first — they are the only ones that treat a missing
    # value as meaningful rather than as "cannot compare".
    if rule.op == "is_null":
        return actual is None
    if rule.op == "is_not_null":
        return actual is not None
    if rule.op == "is_empty":
        return _is_empty(actual)
    if rule.op == "is_not_empty":
        return not _is_empty(actual)

    if rule.op in OPS_WITH_FIELD_VALUE:
        other = context_value(context.values, str(rule.value))
        return _compare_pair(rule.op, actual, other, rule.case_sensitive)

    expected = rule.value

    if rule.op == "eq":
        return _scalar_equal(actual, expected, rule.case_sensitive)
    if rule.op == "neq":
        return not _scalar_equal(actual, expected, rule.case_sensitive)

    if rule.op in {"in", "not_in", "in_case_insensitive", "not_in_case_insensitive"}:
        # The `_case_insensitive` variants exist because published definitions
        # already name them; plain `in` stays case-sensitive to match.
        case_sensitive = rule.op in {"in", "not_in"}
        members = expected if isinstance(expected, list) else []
        hit = any(_scalar_equal(actual, item, case_sensitive) for item in members)
        return hit if rule.op in {"in", "in_case_insensitive"} else not hit

    if rule.op in {"contains", "not_contains"}:
        hit = _contains(actual, expected, rule.case_sensitive)
        return hit if rule.op == "contains" else not hit

    if rule.op in {"starts_with", "ends_with"}:
        haystack = _as_text(actual, rule.case_sensitive)
        needle = _as_text(expected, rule.case_sensitive)
        if haystack is None or needle is None:
            return False
        return (
            haystack.startswith(needle)
            if rule.op == "starts_with"
            else haystack.endswith(needle)
        )

    if rule.op == "matches":
        haystack = _as_text(actual, case_sensitive=True)
        if haystack is None or len(haystack) > _MAX_MATCH_INPUT_LENGTH:
            return False
        flags = 0 if rule.case_sensitive else re.IGNORECASE
        try:
            return re.search(str(expected), haystack, flags) is not None
        except re.error:
            return False

    if rule.op in {"gt", "gte", "lt", "lte"}:
        return _compare_pair(rule.op, actual, expected, rule.case_sensitive)

    if rule.op == "between":
        low, high = (expected or [None, None])[:2]
        return _compare_pair("gte", actual, low, False) and _compare_pair(
            "lte", actual, high, False
        )

    if rule.op in {"before", "after"}:
        left = _as_datetime(actual, context)
        right = _as_datetime(expected, context)
        if left is None or right is None:
            return False
        return left < right if rule.op == "before" else left > right

    if rule.op in {"within", "older_than"}:
        moment = _as_datetime(actual, context)
        window = _parse_duration(str(expected))
        if moment is None or window is None:
            return False
        distance = abs(context.now - moment)
        return distance <= window if rule.op == "within" else distance > window

    if rule.op in {"any_of", "all_of"}:
        members = _as_list(actual)
        expected_items = expected if isinstance(expected, list) else []
        matched = [
            any(_scalar_equal(item, wanted, rule.case_sensitive) for item in members)
            for wanted in expected_items
        ]
        return any(matched) if rule.op == "any_of" else all(matched)

    return False


# ---------------------------------------------------------------------------
# Field paths
# ---------------------------------------------------------------------------


def path_parts(path: str) -> list[str]:
    """Split ``appointment.provider[0].name`` into addressable segments."""
    return [
        part.strip()
        for part in path.replace("[", ".").replace("]", "").split(".")
        if part.strip()
    ]


def context_value(values: Mapping[str, Any], path: str) -> Any:
    """Resolve a dotted path, preferring an exact flat key.

    Run context carries both flat keys (``appointment_status``) and nested
    sections (``appointment.status``), so an exact hit wins before we walk.
    """
    if path in values:
        return values[path]

    current: Any = values
    for part in path_parts(path):
        if isinstance(current, Mapping):
            current = current.get(part)
        elif isinstance(current, (list, tuple)) and part.isdigit():
            index = int(part)
            current = current[index] if 0 <= index < len(current) else None
        else:
            return None
        if current is None:
            return None
    return current


def referenced_fields(expression: Any) -> set[str]:
    """Every context path an expression reads. Used by validation and previews."""
    if isinstance(expression, FilterGroup):
        found: set[str] = set()
        for child in expression.children:
            found |= referenced_fields(child)
        return found
    if isinstance(expression, FilterRule):
        fields = {expression.field}
        if expression.op in OPS_WITH_FIELD_VALUE and isinstance(expression.value, str):
            fields.add(expression.value)
        return fields
    return set()


# ---------------------------------------------------------------------------
# Coercion helpers
# ---------------------------------------------------------------------------


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict, set)):
        return not value
    return False


def _as_text(value: Any, case_sensitive: bool) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, (list, tuple, dict, set)):
        return None
    else:
        text = str(value)
    return text if case_sensitive else text.casefold()


def _as_number(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            return Decimal(value.strip())
        except (InvalidOperation, ValueError):
            return None
    return None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _zone(name: str) -> Any:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return timezone.utc


def _parse_duration(text: str) -> timedelta | None:
    """Parse the ISO-8601 duration subset workflows need (no months/years).

    Months and years are deliberately unsupported: they are not fixed-length, so
    "within P1M" would mean different things in February and March. Authors who
    want a month write P30D and mean it.
    """
    match = _ISO_DURATION_RE.match(text.strip())
    if match is None:
        return None
    parts = {key: int(value) for key, value in match.groupdict(default="0").items()}
    return timedelta(
        weeks=parts["weeks"],
        days=parts["days"],
        hours=parts["hours"],
        minutes=parts["minutes"],
        seconds=parts["seconds"],
    )


def _as_datetime(value: Any, context: EvaluationContext) -> datetime | None:
    """Coerce to an aware UTC datetime, understanding ``now`` and ``now+P3D``."""
    if isinstance(value, datetime):
        return _ensure_aware(value, context)
    if isinstance(value, date):
        return _ensure_aware(
            datetime(value.year, value.month, value.day), context
        )
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None

    relative = _RELATIVE_NOW_RE.match(text)
    if relative is not None:
        offset = timedelta()
        raw_duration = relative.group("duration")
        if raw_duration:
            parsed = _parse_duration(raw_duration)
            if parsed is None:
                return None
            offset = parsed if relative.group("sign") != "-" else -parsed
        return context.now + offset

    if _DATE_ONLY_RE.match(text):
        # A bare date means midnight *at the clinic*, not midnight UTC.
        try:
            parsed_date = date.fromisoformat(text)
        except ValueError:
            return None
        return _ensure_aware(
            datetime(parsed_date.year, parsed_date.month, parsed_date.day), context
        )

    try:
        parsed_dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _ensure_aware(parsed_dt, context)


def _ensure_aware(value: datetime, context: EvaluationContext) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=context.tzinfo)
    return value.astimezone(timezone.utc)


def _scalar_equal(actual: Any, expected: Any, case_sensitive: bool) -> bool:
    if actual is None or expected is None:
        return actual is None and expected is None

    if isinstance(actual, bool) or isinstance(expected, bool):
        actual_bool = _as_bool(actual)
        expected_bool = _as_bool(expected)
        if actual_bool is not None and expected_bool is not None:
            return actual_bool is expected_bool
        return False

    # Numeric comparison first, so context's "1" equals an authored 1.
    actual_number = _as_number(actual)
    expected_number = _as_number(expected)
    if actual_number is not None and expected_number is not None:
        return actual_number == expected_number

    actual_text = _as_text(actual, case_sensitive)
    expected_text = _as_text(expected, case_sensitive)
    if actual_text is None or expected_text is None:
        return actual == expected
    return actual_text.strip() == expected_text.strip()


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().casefold()
        if text in {"true", "yes", "1"}:
            return True
        if text in {"false", "no", "0"}:
            return False
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    return None


def _compare_pair(op: str, left: Any, right: Any, case_sensitive: bool) -> bool:
    if op == "field_eq":
        return _scalar_equal(left, right, case_sensitive)
    if op == "field_neq":
        return not _scalar_equal(left, right, case_sensitive)

    ordering = _ordering(left, right)
    if ordering is None:
        return False
    if op in {"gt", "field_gt"}:
        return ordering > 0
    if op == "gte":
        return ordering >= 0
    if op in {"lt", "field_lt"}:
        return ordering < 0
    if op == "lte":
        return ordering <= 0
    return False


def _ordering(left: Any, right: Any) -> int | None:
    """-1/0/1, or None when the pair is not comparably typed."""
    left_number = _as_number(left)
    right_number = _as_number(right)
    if left_number is not None and right_number is not None:
        return _sign(left_number - right_number)

    # Datetimes compare without a location: both sides are absolute here, and a
    # relative "now" value is only meaningful through before/after.
    utc_context = EvaluationContext(values={})
    left_dt = _as_datetime(left, utc_context)
    right_dt = _as_datetime(right, utc_context)
    if left_dt is not None and right_dt is not None:
        return _sign(Decimal((left_dt - right_dt).total_seconds()))

    left_text = _as_text(left, case_sensitive=False)
    right_text = _as_text(right, case_sensitive=False)
    if left_text is None or right_text is None:
        return None
    if left_text == right_text:
        return 0
    return 1 if left_text > right_text else -1


def _sign(value: Decimal) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _contains(actual: Any, expected: Any, case_sensitive: bool) -> bool:
    if isinstance(actual, (list, tuple, set)):
        return any(_scalar_equal(item, expected, case_sensitive) for item in actual)
    haystack = _as_text(actual, case_sensitive)
    needle = _as_text(expected, case_sensitive)
    if haystack is None or needle is None:
        return False
    return needle in haystack
