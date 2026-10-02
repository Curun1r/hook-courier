import logging

from celery import shared_task
from celery.exceptions import Reject
from kombu.exceptions import OperationalError

from .delivery import perform_delivery

logger = logging.getLogger(__name__)


@shared_task(bind=True, ignore_result=True, max_retries=None)
def deliver_event(self, event_id: str) -> None:
    outcome = perform_delivery(event_id)
    if outcome.retry_after is not None:
        try:
            raise self.retry(countdown=outcome.retry_after, max_retries=None)
        except (OperationalError, Reject, OSError):
            logger.warning("Delivery retry could not be queued; event_id=%s", event_id)
