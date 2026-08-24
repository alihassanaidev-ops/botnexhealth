"""Unit tests for clinic-authored campaign email templates.

Covers the schema contract on ``SendEmailNode`` (inline vs saved template),
publish-time validation, and the executor's use of a saved template.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.models.campaign_email_template import (
    TEMPLATE_KEY_RE,
    slugify_template_key,
)
from src.app.services.automation.definition_schema import SendEmailNode
from src.app.services.campaign_email_template_service import (
    CampaignEmailTemplateError,
    CampaignEmailTemplateService,
    available_merge_fields,
    sample_context,
)


# ---------------------------------------------------------------------------
# Key slug
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Post-Op Day 1", "post-op-day-1"),
        ("  Recall Reminder!  ", "recall-reminder"),
        ("Welcome / New Patient", "welcome-new-patient"),
    ],
)
def test_slugify_template_key(raw, expected):
    assert slugify_template_key(raw) == expected


@pytest.mark.parametrize("key", ["post-op", "recall_2", "a", "welcome1"])
def test_valid_keys(key):
    assert TEMPLATE_KEY_RE.match(key)


@pytest.mark.parametrize("key", ["Post-Op", "-leading", "with space", "", "a" * 81])
def test_invalid_keys(key):
    assert not TEMPLATE_KEY_RE.match(key)


# ---------------------------------------------------------------------------
# Node schema — inline vs saved template
# ---------------------------------------------------------------------------


def test_inline_content_is_still_valid():
    """Definitions published before template_key existed must be unaffected."""
    node = SendEmailNode(
        id="n1",
        subject_template="Hi {{patient_first_name}}",
        body_template="See you soon",
        next_node_id="n2",
    )
    assert node.template_key is None
    assert node.html_template is None


def test_template_key_alone_is_valid():
    node = SendEmailNode(id="n1", template_key="post-op-day-1", next_node_id="n2")
    assert node.template_key == "post-op-day-1"
    assert node.subject_template == ""


def test_template_key_with_inline_content_is_rejected():
    """Two content sources would make it ambiguous which one actually sends."""
    with pytest.raises(ValueError, match="not both"):
        SendEmailNode(
            id="n1",
            template_key="post-op",
            subject_template="Hi",
            body_template="there",
            next_node_id="n2",
        )


def test_missing_subject_is_rejected_in_inline_mode():
    with pytest.raises(ValueError, match="subject_template is required"):
        SendEmailNode(id="n1", body_template="b", next_node_id="n2")


def test_missing_body_is_rejected_in_inline_mode():
    with pytest.raises(ValueError, match="body_template is required"):
        SendEmailNode(id="n1", subject_template="s", next_node_id="n2")


def test_html_template_is_accepted_inline():
    node = SendEmailNode(
        id="n1",
        subject_template="s",
        body_template="b",
        html_template="<p>b</p>",
        next_node_id="n2",
    )
    assert node.html_template == "<p>b</p>"


# ---------------------------------------------------------------------------
# Merge fields available to templates
# ---------------------------------------------------------------------------


def test_sample_context_covers_email_fields():
    ctx = sample_context()
    assert "patient_first_name" in ctx
    assert "clinic_name" in ctx
    assert all(isinstance(v, str) for v in ctx.values())


def test_available_merge_fields_carry_editor_metadata():
    fields = available_merge_fields()
    assert fields
    first = fields[0]
    assert {"name", "label", "description", "sample", "group", "phi_level"} <= set(first)


# ---------------------------------------------------------------------------
# Service validation
# ---------------------------------------------------------------------------


def _service():
    return CampaignEmailTemplateService(AsyncMock())


def test_validate_bodies_rejects_empty():
    with pytest.raises(CampaignEmailTemplateError, match="cannot be empty"):
        CampaignEmailTemplateService._validate_bodies("", "<p>x</p>", "x")


def test_validate_bodies_rejects_bad_syntax():
    with pytest.raises(CampaignEmailTemplateError, match="Invalid syntax"):
        CampaignEmailTemplateService._validate_bodies("{% if %}", "<p>x</p>", "x")


def test_validate_bodies_accepts_valid_templates():
    CampaignEmailTemplateService._validate_bodies(
        "Hi {{patient_first_name}}", "<p>{{clinic_name}}</p>", "{{clinic_name}}"
    )


def test_render_preview_raw_substitutes_sample_values():
    out = CampaignEmailTemplateService.render_preview_raw(
        subject_template="Hi {{patient_first_name}}",
        html_body="<p>{{clinic_name}}</p>",
        text_body="{{clinic_name}}",
    )
    assert "{{" not in out["subject"]
    assert "{{" not in out["html"]
    assert out["text"]


def test_render_preview_is_sandboxed():
    """Template content reaches the same sandboxed environment as the system
    templates — there is one engine, not two."""
    from jinja2.exceptions import SecurityError

    with pytest.raises(SecurityError):
        CampaignEmailTemplateService.render_preview_raw(
            subject_template="{{ ''.__class__.__mro__ }}",
            html_body="<p>x</p>",
            text_body="x",
        )


def test_create_rejects_blank_name():
    svc = _service()
    with pytest.raises(CampaignEmailTemplateError, match="name is required"):
        asyncio.run(
            svc.create(
                "inst-1", name="  ", subject_template="s", html_body="h", text_body="t"
            )
        )


def test_create_rejects_invalid_key():
    svc = _service()
    with pytest.raises(CampaignEmailTemplateError, match="Template key must be"):
        asyncio.run(
            svc.create(
                "inst-1",
                name="Valid",
                key="Not A Key",
                subject_template="s",
                html_body="h",
                text_body="t",
            )
        )


# ---------------------------------------------------------------------------
# Executor — saved template resolution
# ---------------------------------------------------------------------------


def _make_run():
    run = MagicMock()
    run.id = "run-1"
    run.workflow_id = "wf-1"
    run.institution_id = "inst-1"
    run.contact_id = "c-1"
    run.location_id = "l-1"
    run.context = {}
    return run


def _make_executor(contact, institution, saved_template):
    from src.app.services.automation.email_node_executor import EmailNodeExecutor

    session = AsyncMock()
    runtime = AsyncMock()

    async def _get(model, pk):
        from src.app.models.contact import Contact
        from src.app.models.institution import Institution

        if model is Contact:
            return contact
        if model is Institution:
            return institution
        return None

    session.get = AsyncMock(side_effect=_get)
    runtime.already_sent = AsyncMock(return_value=False)
    runtime.begin_step = AsyncMock(return_value=MagicMock())
    runtime.fail_step = AsyncMock()
    runtime.fail_run = AsyncMock()
    runtime.complete_step = AsyncMock()

    svc = MagicMock()
    svc.get_by_key = AsyncMock(return_value=saved_template)
    return EmailNodeExecutor(session, runtime), runtime, svc


def _contact():
    c = MagicMock()
    c.email = "patient@example.com"
    c.first_name = "Jane"
    c.last_name = "Doe"
    c.full_name = None
    c.phone = "+14165551234"
    return c


def _institution():
    inst = MagicMock()
    inst.email_from_address = "clinic@example.com"
    inst.email_from_name = "Clinic"
    return inst


def _saved(active=True):
    t = MagicMock()
    t.is_active = active
    t.subject_template = "Saved subject for {{patient_first_name}}"
    t.text_body = "Saved text"
    t.html_body = "<p>Saved html</p>"
    return t


def _execute(executor, svc, node, run=None):
    captured: dict = {}

    async def _post(url, headers, json):
        captured["payload"] = json
        resp = MagicMock()
        resp.status_code = 200
        resp.json = MagicMock(return_value={"id": "resend-1"})
        return resp

    with patch("src.app.services.automation.email_node_executor.settings") as s:
        s.resend_api_key = "key"
        s.resend_from_email = "platform@scalenexus.ai"
        s.resend_reply_to = None
        s.public_base_url = "https://api.example.com"

        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(side_effect=_post)

        with patch(
            "src.app.services.automation.email_node_executor.httpx.AsyncClient",
            return_value=client,
        ), patch(
            "src.app.services.campaign_email_template_service.CampaignEmailTemplateService",
            return_value=svc,
        ):
            captured["result"] = asyncio.run(executor.execute(run or _make_run(), node, {}))
    return captured


def test_executor_uses_saved_template_content():
    executor, runtime, svc = _make_executor(_contact(), _institution(), _saved())
    node = SendEmailNode(id="n1", template_key="post-op", next_node_id="n2")

    out = _execute(executor, svc, node)

    assert out["payload"]["subject"] == "Saved subject for Jane"
    assert "Saved text" in out["payload"]["text"]
    assert "Saved html" in out["payload"]["html"]
    runtime.fail_run.assert_not_called()


def test_saved_template_sends_multipart_with_both_parts():
    """Never HTML-only — text-only clients and spam filters both want the
    plain part present."""
    executor, _, svc = _make_executor(_contact(), _institution(), _saved())
    node = SendEmailNode(id="n1", template_key="post-op", next_node_id="n2")

    out = _execute(executor, svc, node)

    assert out["payload"]["text"]
    assert out["payload"]["html"]


def test_unsubscribe_footer_added_to_both_parts():
    executor, _, svc = _make_executor(_contact(), _institution(), _saved())
    node = SendEmailNode(id="n1", template_key="post-op", next_node_id="n2")

    out = _execute(executor, svc, node)

    assert "unsubscribe" in out["payload"]["text"].lower()
    assert "unsubscribe" in out["payload"]["html"].lower()


def test_inactive_template_fails_the_step():
    """Publish-time validation blocks this, so reaching it means the template
    was deactivated after the workflow went live."""
    executor, runtime, svc = _make_executor(
        _contact(), _institution(), _saved(active=False)
    )
    node = SendEmailNode(id="n1", template_key="post-op", next_node_id="n2")

    _execute(executor, svc, node)

    runtime.fail_run.assert_called_once()
    assert "missing or inactive" in runtime.fail_run.call_args.kwargs["reason"]


def test_deleted_template_fails_the_step():
    executor, runtime, svc = _make_executor(_contact(), _institution(), None)
    node = SendEmailNode(id="n1", template_key="gone", next_node_id="n2")

    _execute(executor, svc, node)

    runtime.fail_run.assert_called_once()
    assert "missing or inactive" in runtime.fail_run.call_args.kwargs["reason"]


def test_inline_node_sends_text_only_when_no_html_given():
    executor, _, svc = _make_executor(_contact(), _institution(), None)
    node = SendEmailNode(
        id="n1",
        subject_template="Hi {{patient_first_name}}",
        body_template="Body",
        next_node_id="n2",
    )

    out = _execute(executor, svc, node)

    assert out["payload"]["subject"] == "Hi Jane"
    assert "html" not in out["payload"]
