"""
Celery tasks.
"""
from src.app.worker import celery_app
from src.app.services.supabase_service import SupabaseService
import logging

logger = logging.getLogger(__name__)

@celery_app.task(bind=True, max_retries=3)
def task_example(self, word: str):
    logger.info(f"Processing task: {word}")
    return f"Processed {word}"

@celery_app.task(bind=True, max_retries=5, default_retry_delay=60)
def reconcile_supabase_user(self, email: str, tenant_id: str):
    """
    Example background task: Check if user exists in Supabase and matches DB.
    This can be used for the 'Reconciliation' pattern later.
    """
    logger.info(f"Reconciling user {email} for tenant {tenant_id}")
    # implementation placeholder
    pass
