"""CRUD and preview for clinic-authored campaign email templates.

The counterpart to :class:`~src.app.services.email_template_service.EmailTemplateService`,
which owns the five fixed *system* notification templates. These are free-form
and clinic-named, referenced from a ``send_email`` workflow node by ``key``.

Rendering deliberately reuses ``EmailTemplateService.render`` so both stacks go
through the same sandboxed, autoescaping Jinja environment — there is one
template engine on the platform, not two.
"""

from __future__ import annotations

import logging
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.campaign_email_template import (
    MAX_TEMPLATES_PER_INSTITUTION,
    TEMPLATE_KEY_RE,
    CampaignEmailTemplate,
    slugify_template_key,
)
from src.app.services.automation.merge_field_catalog import fields_for
from src.app.services.email_template_service import EmailTemplateService

logger = logging.getLogger(__name__)


class CampaignEmailTemplateError(ValueError):
    """A caller-correctable problem — surfaced as 4xx by the routes."""


def sample_context() -> dict[str, str]:
    """Sample values for every email-capable merge field, for live preview."""
    return {
        field.name: field.sample
        for field in fields_for(channel="email", include_unavailable=True)
    }


def available_merge_fields() -> list[dict[str, str]]:
    """The merge fields an email template may use, for the editor's picker."""
    return [
        {
            "name": field.name,
            "label": field.label,
            "description": field.description,
            "sample": field.sample,
            "group": field.group,
            "phi_level": field.phi_level,
        }
        for field in fields_for(channel="email", include_unavailable=True)
    ]


class CampaignEmailTemplateService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def list_templates(
        self, institution_id: str, *, active_only: bool = False
    ) -> list[CampaignEmailTemplate]:
        query = select(CampaignEmailTemplate).where(
            CampaignEmailTemplate.institution_id == institution_id
        )
        if active_only:
            query = query.where(CampaignEmailTemplate.is_active.is_(True))
        result = await self._session.execute(
            query.order_by(CampaignEmailTemplate.name)
        )
        return list(result.scalars().all())

    async def get_by_key(
        self, institution_id: str, key: str
    ) -> CampaignEmailTemplate | None:
        result = await self._session.execute(
            select(CampaignEmailTemplate).where(
                CampaignEmailTemplate.institution_id == institution_id,
                CampaignEmailTemplate.key == key,
            )
        )
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def create(
        self,
        institution_id: str,
        *,
        name: str,
        subject_template: str,
        html_body: str,
        text_body: str,
        key: str | None = None,
        is_active: bool = True,
    ) -> CampaignEmailTemplate:
        name = (name or "").strip()
        if not name:
            raise CampaignEmailTemplateError("Template name is required")

        resolved_key = (key or slugify_template_key(name)).strip().lower()
        if not TEMPLATE_KEY_RE.match(resolved_key):
            raise CampaignEmailTemplateError(
                "Template key must be lowercase letters, numbers, hyphens or "
                "underscores, starting with a letter or number"
            )

        if await self.get_by_key(institution_id, resolved_key) is not None:
            raise CampaignEmailTemplateError(
                f"A template with key '{resolved_key}' already exists"
            )

        # Bounded so a runaway integration cannot fill a tenant's template list.
        count = await self._session.scalar(
            select(func.count())
            .select_from(CampaignEmailTemplate)
            .where(CampaignEmailTemplate.institution_id == institution_id)
        )
        if (count or 0) >= MAX_TEMPLATES_PER_INSTITUTION:
            raise CampaignEmailTemplateError(
                f"Template limit reached ({MAX_TEMPLATES_PER_INSTITUTION})"
            )

        self._validate_bodies(subject_template, html_body, text_body)

        template = CampaignEmailTemplate(
            id=str(uuid4()),
            institution_id=institution_id,
            key=resolved_key,
            name=name,
            subject_template=subject_template,
            html_body=html_body,
            text_body=text_body,
            is_active=is_active,
        )
        self._session.add(template)
        await self._session.flush()
        return template

    async def update(
        self,
        institution_id: str,
        key: str,
        *,
        name: str | None = None,
        subject_template: str | None = None,
        html_body: str | None = None,
        text_body: str | None = None,
        is_active: bool | None = None,
    ) -> CampaignEmailTemplate | None:
        """Update in place. The key is immutable — published workflow
        definitions reference it, so renaming would break them silently."""
        template = await self.get_by_key(institution_id, key)
        if template is None:
            return None

        self._validate_bodies(
            subject_template if subject_template is not None else template.subject_template,
            html_body if html_body is not None else template.html_body,
            text_body if text_body is not None else template.text_body,
        )

        if name is not None:
            cleaned = name.strip()
            if not cleaned:
                raise CampaignEmailTemplateError("Template name cannot be empty")
            template.name = cleaned
        if subject_template is not None:
            template.subject_template = subject_template
        if html_body is not None:
            template.html_body = html_body
        if text_body is not None:
            template.text_body = text_body
        if is_active is not None:
            template.is_active = is_active

        await self._session.flush()
        return template

    async def delete(self, institution_id: str, key: str) -> bool:
        template = await self.get_by_key(institution_id, key)
        if template is None:
            return False
        await self._session.delete(template)
        await self._session.flush()
        return True

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_bodies(subject: str, html: str, text: str) -> None:
        for label, value in (
            ("subject", subject),
            ("HTML body", html),
            ("text body", text),
        ):
            if not (value or "").strip():
                raise CampaignEmailTemplateError(f"Template {label} cannot be empty")
            error = EmailTemplateService.validate_template(value)
            if error:
                raise CampaignEmailTemplateError(f"Invalid syntax in {label}: {error}")

    @staticmethod
    def render_preview_raw(
        *, subject_template: str, html_body: str, text_body: str
    ) -> dict[str, str]:
        """Render arbitrary content against sample merge values."""
        sample = sample_context()
        render = EmailTemplateService.render
        return {
            "subject": render(subject_template, sample),
            "html": render(html_body, sample),
            "text": render(text_body, sample),
        }

    async def render_preview(
        self, institution_id: str, key: str
    ) -> dict[str, str] | None:
        template = await self.get_by_key(institution_id, key)
        if template is None:
            return None
        return self.render_preview_raw(
            subject_template=template.subject_template,
            html_body=template.html_body,
            text_body=template.text_body,
        )
