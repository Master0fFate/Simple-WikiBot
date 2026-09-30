# Simple WikiBot

A small English Wikipedia bot built with Python 3.11+ and discord.py 2.7. It uses native slash commands and does not read server messages, members, or presence data.

## Commands

- `/wiki arg:<topic> [private:true]`: resolves an exact title (including redirects), then searches Wikipedia if the title does not exist. Returns a bounded introduction, attribution and a **Read on Wikipedia** button
- `/help`: usage and limitations, visible only to the person asking
- `/ping`: a private connection check

Results are public by default, as in the original bot. Use `private:true` for private results, including lookup errors. Disambiguation pages are clearly marked; refine the title or open Wikipedia to choose a meaning. Empty introductions still provide an article link. If Embed Links is unavailable, the bot sends a title and link instead.

## Install and run

```sh
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e .
```

Set `DISCORD_TOKEN` with your hosting provider's secret manager, or in your shell. Never commit or paste the token into source code. `.env.example` documents the settings, but `.env` files are **not automatically loaded**.

```sh
# Enter this in a private local terminal; do not put an actual token in a shared script.
export DISCORD_TOKEN='your-bot-token'
# Optional development server ID: faster, server-only command registration
export DISCORD_GUILD_ID='your-server-id'
python bot_en.py
# Alternatively, after installation: wikibot
```

In PowerShell, environment variables use `$env:DISCORD_TOKEN = 'your-bot-token'`. Omit `DISCORD_GUILD_ID` entirely for global registration. A malformed configuration stops startup with a useful error. The token is not displayed, prompted for, or written to disk.

### Discord application setup

1. Create or use an application in the [Discord Developer Portal](https://discord.com/developers/applications), then obtain its bot token privately
2. Leave all privileged intents disabled. This bot requests **no gateway intents** and needs no administrator, member, presence, or message-content access
3. For server installation, enable Guild Install with `bot` and `applications.commands`. Grant View Channel, Send Messages and Embed Links (plus Send Messages in Threads if using threads)
4. Optionally enable User Install with `applications.commands`. Commands declare support for servers, bot DMs and private channels. Portal installation contexts must also be enabled; code alone cannot enable them
5. Start the bot. Commands sync once during startup, never on reconnect. Global command updates may take time to appear

The bot receives interactions through the Discord gateway. No public HTTP server or interaction endpoint URL is required. Use a separate test application/server before production.

### Configuration

| Environment variable | Meaning |
| --- | --- |
| `DISCORD_TOKEN` | Required bot token |
| `DISCORD_GUILD_ID` | Optional positive development server ID; otherwise sync globally |
| `WIKIPEDIA_USER_AGENT` | Optional identifying HTTP User-Agent, including a real contact URL or email for your deployment |

When moving from development-server sync to global sync, Discord can retain the old server command registrations. Remove obsolete guild registrations deliberately with Discord's API or use a separate development application. Startup never deletes registrations in unrelated scopes.

## Reliability and privacy

- Shared async HTTP session, closed on shutdown, with eight-second request timeouts and a 20-second total lookup/queue deadline
- Serial Wikipedia access paced to at most one request per second, five-second per-user command cooldown, and a five-minute/256-entry in-memory result cache
- HTTP 429/503 and MediaWiki `maxlag` put upstream traffic on hold; numeric Retry-After values are respected between 60 seconds and one hour. Other failures fail safely rather than retrying aggressively
- Query validation, bounded response reads, HTTPS-only fixed API destination and disabled HTTP redirects
- Plain-text extracts, escaped Discord formatting/mentions, UTF-16-aware embed limits, and a link built only on the Wikipedia hostname
- No database, plaintext token storage, or logging of search queries. Queries are still sent to Wikipedia and held transiently in process memory. Discord stores interaction messages according to its own policies
- Results are Wikipedia introductions, not independently fact-checked answers. Attribution/source links are included; see each article for sources, contributors and licensing

Rate limits and caches are process-local. Run one instance; horizontal scaling would need shared coordination. Overload expires queued requests rather than waiting indefinitely. Failures after a public defer appear publicly; select `private:true` when privacy matters.

## Updating from the original bot

The original `/wiki arg:` interface and `python bot_en.py` entry point still work. `requests`, `token.json`, all-intent startup and prefix-command handlers have been removed. Move an existing token to `DISCORD_TOKEN`; the old `token.json` is no longer read. Delete it from your deployment once migrated, and rotate any token that was ever committed or shared.

## Development

```sh
python -m pip install -e '.[dev]'
python -m pytest -q
ruff check .
ruff format --check .
python -m compileall -q wikibot bot_en.py
```

Tests use fake HTTP responses and Discord interactions: no token or live service needed. They exercise redirects/search fallback, cache expiry and bounds, malformed upstream data, network errors, rate limiting, oversized bodies, safe rendering, permissions fallback, private deferral, command registration and shutdown. CI runs the checks on Python 3.11, 3.12 and 3.13 with read-only repository permissions.

Live Discord installation/interaction and live Wikipedia integration are separate smoke checks, not claimed by the unit suite. To smoke-test in your own server, try an exact title, misspelling, ambiguous title, an empty-summary article, `private:true`, rapid repeated calls, and a channel without Embed Links. Never use a production token in CI.

### API references

- [discord.py application commands](https://discordpy.readthedocs.io/en/stable/interactions/api.html)
- [Discord interaction response deadlines](https://docs.discord.com/developers/interactions/receiving-and-responding)
- [MediaWiki search API](https://www.mediawiki.org/wiki/API:Search)
- [TextExtracts API](https://www.mediawiki.org/wiki/Extension:TextExtracts#API)
- [MediaWiki API etiquette and User-Agent policy](https://www.mediawiki.org/wiki/API:Etiquette)

The Action API is used instead of relying on the legacy REST summary endpoint. No scraping is performed.
