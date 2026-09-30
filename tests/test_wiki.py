import json
from unittest.mock import AsyncMock

import aiohttp
import pytest

from wikibot.wiki import Article, NoArticle, WikiBusy, WikiError, Wikipedia, validate_query


class Content:
    def __init__(self, raw):
        self.raw = raw

    async def iter_chunked(self, size):
        for i in range(0, len(self.raw), size):
            yield self.raw[i : i + size]


class Response:
    def __init__(self, data=None, status=200, headers=None, raw=None):
        self.status = status
        self.headers = headers or {}
        self.content = Content(raw if raw is not None else json.dumps(data).encode())

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def page(title="Python", extract="An article.", **kwargs):
    return {"query": {"pages": [{"title": title, "extract": extract, **kwargs}]}}


@pytest.mark.parametrize("query", ["", " ", "a" * 201, "a\nb", "x\x00y"])
def test_query_validation(query):
    with pytest.raises(ValueError):
        validate_query(query)


def test_url_cannot_escape_wikipedia():
    assert Article("../A?# /", "").url == "https://en.wikipedia.org/wiki/..%2FA%3F%23_%2F"


async def test_exact_lookup_and_cache():
    session = Session(Response(page()))
    wiki = Wikipedia(session)
    assert (await wiki.lookup(" Python ")).title == "Python"
    assert await wiki.lookup("Python") == await wiki.lookup("Python")
    assert len(session.calls) == 1
    params = session.calls[0][1]["params"]
    assert params["titles"] == "Python"
    assert params["redirects"] == "1"
    assert params["explaintext"] == "1"
    assert session.calls[0][1]["allow_redirects"] is False


async def test_search_fallback_and_missing():
    wiki = Wikipedia(Session())
    wiki._request = AsyncMock(side_effect=[{"query": {"pages": [{"missing": True}]}}, page()])
    assert (await wiki.lookup("python language")).title == "Python"
    assert wiki._request.call_args.kwargs["generator"] == "search"
    wiki._request = AsyncMock(return_value={})
    with pytest.raises(NoArticle):
        await wiki.lookup("does not exist")


async def test_cache_expiry_and_case_preservation():
    now = [0]
    wiki = Wikipedia(Session(), clock=lambda: now[0])
    wiki._request = AsyncMock(return_value=page())
    await wiki.lookup("Python")
    await wiki.lookup("PYTHON")
    assert wiki._request.call_count == 2
    now[0] = 301
    await wiki.lookup("Python")
    assert wiki._request.call_count == 3


async def test_cache_is_bounded():
    wiki = Wikipedia(Session())
    wiki._request = AsyncMock(return_value=page())
    for i in range(260):
        await wiki.lookup(str(i))
    assert len(wiki.cache) == 256
    assert "0" not in wiki.cache


@pytest.mark.parametrize("status", [429, 503])
async def test_rate_limit_blocks_subsequent_requests(status):
    session = Session(Response(status=status, headers={"Retry-After": "120"}))
    wiki = Wikipedia(session, clock=lambda: 0)
    for _ in range(2):
        with pytest.raises(WikiBusy):
            await wiki.lookup("Python")
    assert wiki.blocked_until == 120
    assert len(session.calls) == 1


@pytest.mark.parametrize(
    "response",
    [
        Response(status=500),
        Response(raw=b"not json"),
        Response(data=[]),
        Response(raw=b"a" * 1_000_001),
        Response(data={"error": {"code": "badvalue"}}),
        aiohttp.ClientConnectionError(),
        TimeoutError(),
    ],
)
async def test_upstream_failures_are_safe(response):
    with pytest.raises(WikiError):
        await Wikipedia(Session(response)).lookup("Python")


async def test_maxlag_and_bad_retry_after():
    wiki = Wikipedia(Session(Response({"error": {"code": "maxlag"}})), clock=lambda: 0)
    with pytest.raises(WikiBusy):
        await wiki.lookup("Python")
    assert wiki.blocked_until == 60
    wiki = Wikipedia(Session(Response(status=429, headers={"Retry-After": "later"})))
    with pytest.raises(WikiBusy):
        await wiki.lookup("Python")


@pytest.mark.parametrize(
    "data",
    [
        {"query": []},
        {"query": {"pages": {}}},
        {"query": {"pages": [None]}},
        page(title=42),
        page(extract=[]),
        page(pageprops=[]),
    ],
)
def test_malformed_pages(data):
    with pytest.raises(WikiError):
        Wikipedia._article(data)


def test_disambiguation_and_empty_extract():
    assert Wikipedia._article(page(pageprops={"disambiguation": ""})).disambiguation
    assert Wikipedia._article(page(extract="")).extract == ""


async def test_pipe_does_not_inject_extra_titles():
    wiki = Wikipedia(Session())
    wiki._request = AsyncMock(return_value=page())
    await wiki.lookup("A|B")
    wiki._request.assert_awaited_once_with(
        generator="search", gsrsearch="A|B", gsrnamespace="0", gsrlimit="1"
    )


async def test_upstream_requests_are_paced(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr("wikibot.wiki.asyncio.sleep", sleep)
    wiki = Wikipedia(Session(Response(page()), Response(page())), clock=lambda: 0)
    await wiki.lookup("One")
    await wiki.lookup("Two")
    sleep.assert_awaited_once_with(1.0)


def test_huge_article_url_is_rejected():
    with pytest.raises(WikiError):
        Wikipedia._article(page(title="😀" * 1000))
