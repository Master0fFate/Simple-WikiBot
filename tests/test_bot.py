from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from wikibot.bot import Settings, WikiBot, article_embed, safe_text, send_error, wiki
from wikibot.wiki import Article, WikiError


def test_missing_token(monkeypatch):
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    with pytest.raises(ValueError, match="DISCORD_TOKEN"):
        Settings.from_env()


@pytest.mark.parametrize("value", ["-1", "0", "hello", "١٢"])
def test_bad_guild_id(monkeypatch, value):
    monkeypatch.setenv("DISCORD_TOKEN", "test")
    monkeypatch.setenv("DISCORD_GUILD_ID", value)
    with pytest.raises(ValueError, match="DISCORD_GUILD_ID"):
        Settings.from_env()


def test_secret_not_in_repr():
    assert "secret" not in repr(Settings("secret"))


def test_safe_embeds():
    embed = article_embed(Article("😀" * 300, "@everyone **bold** " * 1000, True))
    assert len(embed.title.encode("utf-16-le")) // 2 <= 256
    assert len(embed.description.encode("utf-16-le")) // 2 <= 3900
    assert "@everyone" not in embed.description
    assert len(embed) < 6000
    assert "several meanings" in embed.description
    assert "No summary" in article_embed(Article("Empty", "")).description
    assert safe_text("**a**", 100) == r"\*\*a\*\*"


async def test_minimal_intents_and_command_schema():
    async with WikiBot(Settings("test")) as bot:
        assert bot.intents.value == 0
        assert {c.name for c in bot.tree.get_commands()} == {"wiki", "help", "ping"}
        schema = bot.tree.get_command("wiki").to_dict(bot.tree)
        assert schema["integration_types"] == [0, 1]
        assert schema["contexts"] == [0, 1, 2]
        assert schema["options"][0]["name"] == "arg"
        assert schema["options"][0]["max_length"] == 200


def interaction(bot, *, embeds=True):
    return SimpleNamespace(
        client=bot,
        response=SimpleNamespace(
            defer=AsyncMock(), send_message=AsyncMock(), is_done=lambda: False
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
        app_permissions=discord.Permissions(embed_links=embeds),
    )


async def test_defer_before_lookup_and_private_response():
    async with WikiBot(Settings("test")) as bot:
        ctx = interaction(bot)

        async def lookup(query):
            ctx.response.defer.assert_awaited_once_with(thinking=True, ephemeral=True)
            return Article("Python", "A language")

        bot.wikipedia = SimpleNamespace(lookup=lookup)
        await wiki.callback(ctx, "Python", True)
        kwargs = ctx.edit_original_response.call_args.kwargs
        assert kwargs["embed"].title == "Python"
        assert kwargs["view"].children[0].url == "https://en.wikipedia.org/wiki/Python"


async def test_validation_before_defer():
    async with WikiBot(Settings("test")) as bot:
        ctx = interaction(bot)
        await wiki.callback(ctx, " ")
        ctx.response.defer.assert_not_awaited()
        assert ctx.response.send_message.call_args.kwargs["ephemeral"] is True


async def test_upstream_error_finishes_deferred_response():
    async with WikiBot(Settings("test")) as bot:
        ctx = interaction(bot)
        bot.wikipedia = SimpleNamespace(lookup=AsyncMock(side_effect=WikiError("Try again")))
        await wiki.callback(ctx, "Python")
        assert ctx.edit_original_response.call_args.kwargs["content"] == "Try again"


async def test_missing_embed_permission_falls_back():
    async with WikiBot(Settings("test")) as bot:
        ctx = interaction(bot, embeds=False)
        bot.wikipedia = SimpleNamespace(lookup=AsyncMock(return_value=Article("Python", "summary")))
        await wiki.callback(ctx, "Python")
        assert (
            "https://en.wikipedia.org/wiki/Python"
            in ctx.edit_original_response.call_args.kwargs["content"]
        )


async def test_error_after_response_uses_followup():
    ctx = interaction(None)
    ctx.response.is_done = lambda: True
    await send_error(ctx, "Try again")
    ctx.followup.send.assert_awaited_once()
    ctx.response.send_message.assert_not_awaited()


async def test_setup_syncs_once_and_close_releases_session():
    bot = WikiBot(Settings("test", 123))
    bot.tree.sync = AsyncMock(return_value=[])
    await bot.setup_hook()
    bot.tree.sync.assert_awaited_once_with(guild=discord.Object(id=123))
    session = bot.session
    await bot.close()
    assert session.closed


async def test_sync_failure_closes_session():
    bot = WikiBot(Settings("test"))
    bot.tree.sync = AsyncMock(side_effect=RuntimeError("Cannot sync"))
    with pytest.raises(RuntimeError):
        await bot.setup_hook()
    assert bot.session.closed
    await bot.close()


async def test_cooldown_rejects_repeated_calls():
    async with WikiBot(Settings("test")) as bot:
        ctx = interaction(bot)
        ctx.user = SimpleNamespace(id=123456)
        import datetime

        ctx.created_at = datetime.datetime.now(datetime.UTC)
        await wiki._check_can_run(ctx)
        from discord import app_commands

        with pytest.raises(app_commands.CommandOnCooldown):
            await wiki._check_can_run(ctx)
