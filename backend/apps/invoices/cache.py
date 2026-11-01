import hashlib
import logging
from urllib.parse import urlencode
from uuid import uuid4

from django.core.cache import cache
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

COUNT_TIMEOUT = 300
# Only parameters that change the total. Page and ordering share one cached count.
COUNT_FILTERS = (
    "search",
    "status",
    "invoice_date__gte",
    "invoice_date__lte",
    "amount__gte",
    "amount__lte",
)


def _version_key(merchant_id):
    return f"invoices:version:{merchant_id}"


def cached_count(merchant_id, params, compute):
    """Return a filtered invoice total, falling back to the database if Redis fails."""
    filters = [(name, value) for name in COUNT_FILTERS for value in params.getlist(name)]
    digest = hashlib.sha256(urlencode(filters).encode()).hexdigest()
    try:
        # Read before counting, so a count that races a new batch is stored
        # under the old version and is never read again.
        version = cache.get_or_set(_version_key(merchant_id), uuid4().hex, None)
        key = f"invoices:count:{merchant_id}:{version}:{digest}"
        count = cache.get(key)
        if count is None:
            count = compute()
            cache.set(key, count, COUNT_TIMEOUT)
        return count
    except RedisError:
        logger.warning("Invoice count cache is unavailable", exc_info=True)
        return compute()


def invalidate_invoice_counts(merchant_id):
    """Orphan every cached count for a merchant by dropping their version."""
    try:
        cache.delete(_version_key(merchant_id))
    except RedisError:
        # The stored counts still expire after COUNT_TIMEOUT.
        logger.warning("Invoice count cache was not invalidated", exc_info=True)
