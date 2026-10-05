"""Unit tests for per-location recall configuration parsing."""

from __future__ import annotations

from types import SimpleNamespace

from src.app.services.automation.recall_config import (
    DEFAULT_DUE_WITHIN_DAYS,
    DEFAULT_INCLUDE_OVERDUE,
    parse_recall_config,
    recall_config_for_location,
)


def test_unset_config_yields_legacy_defaults():
    config = recall_config_for_location(SimpleNamespace(recall_config=None))
    assert config.approved_recall_type_ids == ()
    assert config.has_allowlist is False
    assert config.due_within_days == DEFAULT_DUE_WITHIN_DAYS
    assert config.include_overdue is DEFAULT_INCLUDE_OVERDUE
    # No allowlist means every recall type is still allowed.
    assert config.allows_recall_type("2288000") is True


def test_parses_allowlist_window_mapping_and_providers():
    config = parse_recall_config(
        {
            "approved_recall_type_ids": ["2288000", "2288006", "2288000"],
            "due_within_days": 45,
            "include_overdue": False,
            "appointment_type_map": {"2288000": "nh-1230522"},
            "provider_ids": ["nh-502294485", "nh-506291620"],
        }
    )
    assert config is not None
    assert config.approved_recall_type_ids == ("2288000", "2288006")
    assert config.due_within_days == 45
    assert config.include_overdue is False
    assert config.appointment_type_id_for("2288000") == "nh-1230522"
    assert config.provider_ids == ("nh-502294485", "nh-506291620")


def test_allowlist_rejects_unknown_and_missing_types():
    config = parse_recall_config({"approved_recall_type_ids": ["2288000"]})
    assert config.allows_recall_type("2288000") is True
    assert config.allows_recall_type("999") is False
    assert config.allows_recall_type(None) is False


def test_non_mapping_payload_is_ignored():
    assert parse_recall_config(None) is None
    assert parse_recall_config(["not", "a", "dict"]) is None


def test_due_within_days_is_clamped_and_defaulted():
    assert parse_recall_config({"due_within_days": "not-a-number"}).due_within_days == (
        DEFAULT_DUE_WITHIN_DAYS
    )
    assert parse_recall_config({"due_within_days": -5}).due_within_days == 0
    assert parse_recall_config({"due_within_days": 9999}).due_within_days == 365
