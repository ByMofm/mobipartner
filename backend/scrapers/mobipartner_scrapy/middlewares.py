import logging
import random
import time

from scrapy import signals
from scrapy.exceptions import IgnoreRequest
from twisted.internet import reactor, defer
from twisted.internet.error import TimeoutError as TwistedTimeoutError

logger = logging.getLogger(__name__)


class RotateUserAgentMiddleware:
    def __init__(self, user_agents):
        self.user_agents = user_agents

    @classmethod
    def from_crawler(cls, crawler):
        user_agents = crawler.settings.getlist("USER_AGENTS", [])
        middleware = cls(user_agents)
        crawler.signals.connect(middleware.spider_opened, signal=signals.spider_opened)
        return middleware

    def spider_opened(self, spider):
        spider.logger.info("RotateUserAgentMiddleware enabled")

    def process_request(self, request, spider):
        if self.user_agents:
            request.headers["User-Agent"] = random.choice(self.user_agents)


class PlaywrightRetryMiddleware:
    """Retry requests that fail due to Playwright timeouts or browser errors.

    Works alongside Scrapy's built-in RetryMiddleware (which only handles HTTP
    error codes). This middleware catches Playwright-specific exceptions like
    TimeoutError, browser crashes, and context errors.
    """

    MAX_RETRIES = 2
    BACKOFF_DELAYS = [5, 10]  # seconds

    @classmethod
    def from_crawler(cls, crawler):
        middleware = cls()
        return middleware

    def process_exception(self, request, exception, spider):
        exc_name = type(exception).__name__
        exc_module = type(exception).__module__ or ""

        # Match Playwright errors: TimeoutError, Error from playwright
        is_playwright_error = (
            "playwright" in exc_module.lower()
            or "TimeoutError" in exc_name
            or isinstance(exception, TwistedTimeoutError)
        )

        if not is_playwright_error:
            return None

        retry_count = request.meta.get("playwright_retry_count", 0)

        if retry_count >= self.MAX_RETRIES:
            spider.logger.error(
                f"Playwright retry exhausted ({retry_count}/{self.MAX_RETRIES}): "
                f"{request.url} — {exception}"
            )
            spider.crawler.stats.inc_value("playwright_retry/exhausted")
            return None  # Let the error propagate

        retry_count += 1
        delay = self.BACKOFF_DELAYS[min(retry_count - 1, len(self.BACKOFF_DELAYS) - 1)]

        spider.logger.warning(
            f"Playwright retry {retry_count}/{self.MAX_RETRIES} after {delay}s: "
            f"{request.url} — {exception}"
        )
        spider.crawler.stats.inc_value("playwright_retry/count")

        # Create a new request with incremented retry count
        new_request = request.copy()
        new_request.meta["playwright_retry_count"] = retry_count
        new_request.dont_filter = True

        # Use deferred delay for backoff
        d = defer.Deferred()
        reactor.callLater(delay, d.callback, new_request)
        return d


class CircuitBreakerMiddleware:
    """Closes spider after too many consecutive errors.

    Prevents spiders from wasting time when a site is down or blocking us.
    Resets counter on each successful response.
    """

    MAX_CONSECUTIVE_ERRORS = 10

    def __init__(self):
        self._consecutive_errors = {}
        self._tripped = set()

    @classmethod
    def from_crawler(cls, crawler):
        middleware = cls()
        middleware.crawler = crawler
        return middleware

    def process_response(self, request, response, spider):
        # Reset on successful response (2xx/3xx)
        if response.status < 400:
            self._consecutive_errors[spider.name] = 0
        else:
            self._record_error(spider, f"HTTP {response.status}")
        return response

    def process_exception(self, request, exception, spider):
        self._record_error(spider, str(exception))
        return None

    def _record_error(self, spider, reason):
        if spider.name in self._tripped:
            return

        count = self._consecutive_errors.get(spider.name, 0) + 1
        self._consecutive_errors[spider.name] = count

        if count >= self.MAX_CONSECUTIVE_ERRORS:
            self._tripped.add(spider.name)
            spider.logger.error(
                f"Circuit breaker tripped after {count} consecutive errors "
                f"(last: {reason}). Closing spider."
            )
            spider.crawler.stats.set_value("circuit_breaker/tripped", True)
            self.crawler.engine.close_spider(spider, "circuit_breaker")
