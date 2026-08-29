"""Location scoping for staff SMS recipients.

Mirrors ``resolve_staff_recipients`` on the email side: an institution-wide
recipient (``location_id IS NULL``) receives alerts for every location, while a
location-bound recipient only receives alerts for calls at that location.
Without this a two-site clinic texts both sites about every call.
"""

from __future__ import annotations

import inspect

from src.app.services.sms_notification_recipients import (
    resolve_sms_notification_recipients,
    unique_phone_numbers,
)


class TestResolverContract:
    def test_accepts_a_location_id(self):
        params = inspect.signature(resolve_sms_notification_recipients).parameters
        assert "location_id" in params

    def test_location_is_optional_and_defaults_to_institution_wide_only(self):
        """A call we can't place a location for must not fan out to every site,
        so the default is None — institution-wide recipients only."""
        params = inspect.signature(resolve_sms_notification_recipients).parameters
        assert params["location_id"].default is None

    def test_location_is_keyword_only(self):
        params = inspect.signature(resolve_sms_notification_recipients).parameters
        assert params["location_id"].kind is inspect.Parameter.KEYWORD_ONLY


class TestDeduplication:
    """The same number may be subscribed institution-wide and per-location;
    it must still be texted once."""

    def test_duplicates_collapse(self):
        assert unique_phone_numbers(["+15195550001", "+15195550001"]) == ["+15195550001"]

    def test_order_is_preserved(self):
        assert unique_phone_numbers(["+15195550002", "+15195550001"]) == [
            "+15195550002",
            "+15195550001",
        ]

    def test_blanks_dropped(self):
        assert unique_phone_numbers([None, "", "+15195550001"]) == ["+15195550001"]


class TestModelScope:
    def test_location_column_is_nullable(self):
        from src.app.models.external_sms_notification_recipient import (
            ExternalSmsNotificationRecipient,
        )

        column = ExternalSmsNotificationRecipient.__table__.c.location_id
        assert column.nullable, "NULL is the institution-wide sentinel"

    def test_location_fk_cascades_with_the_location(self):
        from src.app.models.external_sms_notification_recipient import (
            ExternalSmsNotificationRecipient,
        )

        fk = next(iter(ExternalSmsNotificationRecipient.__table__.c.location_id.foreign_keys))
        assert fk.column.table.name == "institution_locations"
        assert fk.ondelete == "CASCADE"
