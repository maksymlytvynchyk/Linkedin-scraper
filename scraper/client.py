import logging
import random
import time

from django.conf import settings

logger = logging.getLogger("scraper.client")

JOBS_SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
JOB_DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
COMPANY_URL = "https://www.linkedin.com/company/{slug}"

# LinkedIn answers 999 (a non-standard code) when it decides you are a bot.
BOT_CODES = {403, 429, 999}


class LinkedInError(RuntimeError):
    """Raised when a request cannot be completed."""


class BlockedError(LinkedInError):
    """Raised when LinkedIn asks us to stop (429/999/403 after all retries)."""


class RateLimiter:
    """Ensure a minimum interval between two requests, with jitter."""

    def __init__(self, min_delay: float = 0.8):
        self.min_delay = max(0.0, min_delay)
        self._next_allowed = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        if now < self._next_allowed:
            time.sleep(self._next_allowed - now)
        jitter = self.min_delay * random.uniform(-0.3, 0.3) if self.min_delay else 0.0
        self._next_allowed = time.monotonic() + max(0.0, self.min_delay + jitter)

    def penalise(self, seconds: float) -> None:
        """Called after a block: push the next allowed request further out."""
        self._next_allowed = max(self._next_allowed, time.monotonic() + seconds)


class LinkedInClient:
    """Small throttled, retrying session around the three guest endpoints."""

    def __init__(
        self,
        *,
        min_delay: float | None = None,
        max_retries: int | None = None,
        proxy: str | None = None,
        user_agent: str | None = None,
        timeout: int = 30,
    ):
        try:
            import requests
        except ImportError as exc:  # pragma: no cover
            raise LinkedInError("`requests` is required: pip install requests") from exc

        self._requests = requests
        self.timeout = timeout
        self.max_retries = int(max_retries if max_retries is not None else getattr(settings, "LINKEDIN_MAX_RETRIES", 4))
        self.limiter = RateLimiter(
            min_delay if min_delay is not None else getattr(settings, "LINKEDIN_MIN_DELAY", 0.8)
        )
        proxy = proxy if proxy is not None else getattr(settings, "LINKEDIN_PROXY_URL", "")
        user_agent = user_agent or getattr(settings, "LINKEDIN_USER_AGENT", "Mozilla/5.0")

        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.linkedin.com/jobs/search/",
                "Connection": "keep-alive",
            }
        )
        if proxy:
            self.session.proxies.update({"http": proxy, "https": proxy})

        self.stats = {"requests": 0, "retries": 0, "blocks": 0, "empty": 0}

    # low level
    def get(self, url: str, params: dict | None = None) -> str:
        last_error: Exception | None = None
        blocked_at: float | None = None

        for attempt in range(1, self.max_retries + 1):
            self.limiter.wait()
            self.stats["requests"] += 1
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
            except self._requests.RequestException as exc:
                last_error = exc
                logger.warning("network error (attempt %s/%s): %s", attempt, self.max_retries, exc)
                self._backoff(attempt, reason="network")
                continue

            if response.status_code == 200:
                content_type = response.headers.get("content-type", "")
                if "json" in content_type and not response.text.strip():
                    self.stats["empty"] += 1
                    return ""
                return response.text

            if response.status_code in BOT_CODES:
                self.stats["blocks"] += 1
                blocked_at = time.monotonic()
                retry_after = response.headers.get("retry-after")
                try:
                    delay = float(retry_after) if retry_after else 0.0
                except ValueError:
                    delay = 0.0
                delay = max(delay, 3.0 * attempt)
                logger.warning(
                    "rate limited/blocked (HTTP %s, attempt %s/%s), sleeping %.1fs",
                    response.status_code,
                    attempt,
                    self.max_retries,
                    delay,
                )
                self.limiter.penalise(delay)
                self._backoff(attempt, reason=f"http_{response.status_code}", floor=delay)
                last_error = BlockedError(f"HTTP {response.status_code}")
                continue

            if 500 <= response.status_code < 600:
                logger.warning("server error HTTP %s (attempt %s), retrying", response.status_code, attempt)
                self._backoff(attempt, reason="server")
                last_error = LinkedInError(f"HTTP {response.status_code}")
                continue

            # 4xx that retrying will not fix -> let the caller decide.
            response.raise_for_status()

        message = f"request failed after {self.max_retries} attempts: {url}"
        if blocked_at is not None and time.monotonic() - blocked_at < 60:
            raise BlockedError(message) from last_error
        raise LinkedInError(message) from last_error

    @staticmethod
    def _backoff(attempt: int, *, reason: str, floor: float = 0.0) -> None:
        delay = max(floor, min(60.0, 2 ** attempt + random.uniform(0.0, 1.0)))
        logger.debug("backoff %.1fs after %s", delay, reason)
        time.sleep(delay)

    # endpoints
    def search_page(
        self,
        *,
        keywords: str,
        location: str = "",
        start: int = 0,
        geo_id: str = "",
        posted: str = "",
        remote: str = "",
        experience: str = "",
        extra_params: dict | None = None,
    ) -> str:
        params: dict[str, object] = {"keywords": keywords, "start": int(start)}
        if location:
            params["location"] = location
        if geo_id:
            params["geoId"] = geo_id
        if posted:
            params["f_TPR"] = posted
        if remote:
            params["f_WT"] = remote
        if experience:
            params["f_E"] = experience
        for key, value in (extra_params or {}).items():
            if value not in (None, ""):
                params[key] = value
        return self.get(JOBS_SEARCH_URL, params=params)

    def job_detail(self, job_id: str) -> tuple[str, int]:
        """Return (html, status_code). A 404 means the posting is gone."""
        url = JOB_DETAIL_URL.format(job_id=job_id)
        try:
            html = self.get(url)
        except self._requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            return "", status
        return html, 200

    def company_page(self, slug: str) -> tuple[str, int]:
        url = COMPANY_URL.format(slug=slug)
        try:
            html = self.get(url)
        except self._requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            return "", status
        return html, 200
