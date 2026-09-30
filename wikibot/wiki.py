"""Bounded, asynchronous MediaWiki Action API client (English Wikipedia)."""

import asyncio
import json
import time
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from urllib.parse import quote

import aiohttp

API_URL = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "SimpleWikiBot/2.0 (https://github.com/Master0fFate/Simple-WikiBot)"


class WikiError(Exception):
    """An upstream failure safe to describe without leaking response bodies."""


class NoArticle(WikiError):
    """No matching article exists."""


class WikiBusy(WikiError):
    """The upstream asked us to slow down."""


def validate_query(query: str) -> str:
    query = query.strip()
    if not 1 <= len(query) <= 200 or any(unicodedata.category(c) == "Cc" for c in query):
        raise ValueError("Enter a topic between 1 and 200 characters, without control characters.")
    return query


@dataclass(frozen=True)
class Article:
    title: str
    extract: str
    disambiguation: bool = False

    @property
    def url(self) -> str:
        return "https://en.wikipedia.org/wiki/" + quote(self.title.replace(" ", "_"), safe="")


class Wikipedia:
    def __init__(self, session: aiohttp.ClientSession, *, clock=time.monotonic):
        self.session = session
        self.clock = clock
        self.cache: OrderedDict[str, tuple[float, Article]] = OrderedDict()
        self.lock = asyncio.Lock()  # Serialize upstream traffic, including cache misses.
        self.blocked_until = 0.0
        self.next_request = 0.0

    async def lookup(self, query: str) -> Article:
        query = validate_query(query)
        # Exact titles can be case-sensitive after the first character.
        async with asyncio.timeout(20):
            async with self.lock:
                cached = self.cache.get(query)
                if cached and cached[0] > self.clock():
                    self.cache.move_to_end(query)
                    return cached[1]
                if self.blocked_until > self.clock():
                    raise WikiBusy("Wikipedia is busy. Please try again in a little while.")
                # Prefer exact titles/redirects; fall back to real full-text search.
                # A pipe is a MediaWiki multi-title delimiter, never a literal title.
                data = await self._request(titles=query, redirects="1") if "|" not in query else {}
                article = self._article(data)
                if article is None:
                    data = await self._request(
                        generator="search", gsrsearch=query, gsrnamespace="0", gsrlimit="1"
                    )
                    article = self._article(data)
                if article is None:
                    raise NoArticle(
                        "No article found. Try a more specific topic or another spelling."
                    )
                self.cache[query] = (self.clock() + 300, article)
                self.cache.move_to_end(query)
                while len(self.cache) > 256:
                    self.cache.popitem(last=False)
                return article

    async def _request(self, **params: str) -> dict:
        delay = self.next_request - self.clock()
        if delay > 0:
            await asyncio.sleep(delay)
        self.next_request = self.clock() + 1.0
        params.update(
            action="query",
            format="json",
            formatversion="2",
            prop="extracts|pageprops",
            exintro="1",
            explaintext="1",
            exchars="3500",
            ppprop="disambiguation",
            maxlag="5",
        )
        try:
            async with self.session.get(
                API_URL,
                params=params,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=8),
            ) as response:
                if response.status in (429, 503):
                    try:
                        delay = float(response.headers.get("Retry-After", "60"))
                    except ValueError:
                        delay = 60
                    self.blocked_until = self.clock() + max(60, min(delay, 3600))
                    raise WikiBusy("Wikipedia is busy. Please try again in a little while.")
                if response.status != 200:
                    raise WikiError("Wikipedia is unavailable right now. Please try again later.")
                raw = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    raw.extend(chunk)
                    if len(raw) > 1_000_000:
                        raise WikiError("Wikipedia returned an unexpectedly large response.")
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError("Expected an object")
                if "error" in data:
                    if isinstance(data["error"], dict) and data["error"].get("code") == "maxlag":
                        self.blocked_until = self.clock() + 60
                        raise WikiBusy("Wikipedia is busy. Please try again in a little while.")
                    raise WikiError("Wikipedia could not process that lookup. Try another topic.")
                return data
        except (aiohttp.ClientError, TimeoutError, ValueError, UnicodeError) as error:
            raise WikiError("Wikipedia could not be reached. Please try again later.") from error

    @staticmethod
    def _article(data: dict) -> Article | None:
        query = data.get("query", {})
        if not isinstance(query, dict):
            raise WikiError("Wikipedia returned an unexpected response.")
        pages = query.get("pages", [])
        if not isinstance(pages, list):
            raise WikiError("Wikipedia returned an unexpected response.")
        for page in pages:
            if not isinstance(page, dict):
                raise WikiError("Wikipedia returned an unexpected response.")
            if "missing" in page or "invalid" in page:
                continue
            title, extract = page.get("title"), page.get("extract", "")
            if not isinstance(title, str) or not title or not isinstance(extract, str):
                raise WikiError("Wikipedia returned an unexpected response.")
            props = page.get("pageprops", {})
            if not isinstance(props, dict):
                raise WikiError("Wikipedia returned an unexpected response.")
            article = Article(title, extract, "disambiguation" in props)
            if len(article.url) > 2048:
                raise WikiError("Wikipedia returned an unexpectedly long article title.")
            return article
        return None
