"""Lead, contact, and patient are lifecycle states of one Contact row."""

from src.app.database import Base
from src.app.models.contact import Contact, LeadStatus


TABLE = Base.metadata.tables["contacts"]


def test_there_is_no_second_person_table() -> None:
    assert "campaign_enquiries" not in Base.metadata.tables


def test_lead_fields_live_on_contacts() -> None:
    for column in (
        "lead_source",
        "lead_status",
        "intake_key",
        "attribution",
        "external_ref",
        "notes_encrypted",
    ):
        assert column in TABLE.c


def test_lead_status_vocabulary_is_contact_owned() -> None:
    assert {status.value for status in LeadStatus} == {
        "new",
        "engaged",
        "qualified",
        "not_qualified",
        "unreachable",
        "booked",
        "handed_to_staff",
    }


def test_contact_phi_is_encrypted_and_hashes_follow_values() -> None:
    contact = Contact()
    contact.phone = "+14165551234"
    contact.email = "someone@example.com"
    assert contact.phone_encrypted != "+14165551234"
    assert contact.email_encrypted != "someone@example.com"
    assert contact.phone_hash
    assert contact.email_hash
    assert contact.phone == "+14165551234"
    assert contact.email == "someone@example.com"
