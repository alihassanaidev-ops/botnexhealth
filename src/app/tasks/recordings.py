"""Background task for downloading Retell call recordings and uploading to S3."""

from __future__ import annotations

import logging

import boto3
import httpx

from src.app.config import settings
from src.app.services.event_bus import publish_event
from src.app.worker import celery_app

logger = logging.getLogger(__name__)


def _get_s3_client():
    return boto3.client(
        "s3",
        region_name=settings.aws_region,
    )


def _s3_key(institution_id: str, call_id: str) -> str:
    return f"recordings/{institution_id}/{call_id}.wav"


@celery_app.task(
    name="src.app.tasks.recordings.upload_recording_to_s3",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    retry_kwargs={"max_retries": 5},
)
def upload_recording_to_s3(
    self,
    call_id: str,
    institution_id: str,
    recording_url: str,
) -> None:
    """Download recording from Retell URL and upload to S3."""
    import asyncio

    asyncio.run(
        _upload_recording_async(
            call_id=call_id,
            institution_id=institution_id,
            recording_url=recording_url,
        )
    )


async def _upload_recording_async(
    call_id: str,
    institution_id: str,
    recording_url: str,
) -> None:
    # Download audio into memory
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.get(recording_url)
        response.raise_for_status()
        audio_bytes = response.content

    # Upload to S3
    s3 = _get_s3_client()
    key = _s3_key(institution_id, call_id)

    content_type = "audio/wav"
    if recording_url.endswith(".mp3"):
        content_type = "audio/mpeg"

    s3.put_object(
        Bucket=settings.aws_s3_bucket_name,
        Key=key,
        Body=audio_bytes,
        ContentType=content_type,
    )

    s3_url = f"https://{settings.aws_s3_bucket_name}.s3.{settings.aws_region}.amazonaws.com/{key}"

    # Update the call record with the S3 URL
    from src.app.database import get_system_db_session, init_database, is_database_initialized
    from src.app.models.call import Call
    from sqlalchemy import select

    if not is_database_initialized():
        init_database(settings.database_url)

    async with get_system_db_session(
        "celery",
        institution_id=institution_id,
        external_id=call_id,
    ) as session:
        call = (
            await session.execute(
                select(Call).where(
                    Call.id == call_id,
                    Call.institution_id == institution_id,
                )
            )
        ).scalar_one_or_none()

        if call:
            call.recording_url = s3_url
            await session.commit()

    logger.info(
        "Recording uploaded to S3: call=%s key=%s size=%d bytes",
        call_id,
        key,
        len(audio_bytes),
    )

    try:
        publish_event(institution_id, "calls_updated")
    except Exception:
        logger.warning(
            "Failed to publish calls_updated SSE event: call=%s institution=%s",
            call_id,
            institution_id,
            exc_info=True,
        )


def generate_presigned_url(s3_url: str, expires_in: int = 3600) -> str | None:
    """Generate a presigned URL for an S3 recording.

    Args:
        s3_url: The stored S3 URL (e.g. https://bucket.s3.region.amazonaws.com/key)
        expires_in: Expiry in seconds (default 1 hour).

    Returns:
        A presigned URL, or None if the URL can't be parsed or signing fails.
    """
    if not s3_url or not settings.aws_s3_bucket_name:
        return None
    try:
        # Extract key from the stored URL
        prefix = f"https://{settings.aws_s3_bucket_name}.s3.{settings.aws_region}.amazonaws.com/"
        if not s3_url.startswith(prefix):
            return None
        key = s3_url[len(prefix):]

        s3 = _get_s3_client()
        return s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.aws_s3_bucket_name, "Key": key},
            ExpiresIn=expires_in,
        )
    except Exception:
        logger.exception("Failed to generate presigned URL for %s", s3_url)
        return None


def enqueue_recording_upload(
    *,
    call_id: str,
    institution_id: str,
    recording_url: str,
) -> None:
    """Queue a background task to upload a call recording to S3."""
    if not settings.celery_broker_url:
        logger.warning("CELERY_BROKER_URL not set. Skipping recording upload.")
        return

    if not settings.aws_s3_bucket_name:
        logger.warning("AWS_S3_BUCKET_NAME not set. Skipping recording upload.")
        return

    upload_recording_to_s3.apply_async(
        kwargs={
            "call_id": call_id,
            "institution_id": institution_id,
            "recording_url": recording_url,
        },
        queue="notifications_default",
    )
