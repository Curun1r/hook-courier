import logging

from kombu.exceptions import OperationalError

from .tasks import deliver_event

logger = logging.getLogger(__name__)


def enqueue_event(event_id: str) -> bool:
    try:
        deliver_event.apply_async(args=[event_id])
    except (OperationalError, OSError):
        logger.warning("Delivery could not be queued; event_id=%s", event_id)
        return False
    return True
