"""Discord slash commands, configuration and resource lifecycle."""

import logging
import os
from dataclasses import dataclass, field

import aiohttp
import discord
from discord import app_commands

from wikibot.wiki import USER_AGENT, Article, WikiError, Wikipedia, validate_query

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Settings:
    token: str = field(repr=False)
    guild_id: int | None = None
    user_agent: str = USER_AGENT

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.environ.get("DISCORD_TOKEN", "").strip()
        if not token:
            raise ValueError("Set DISCORD_TOKEN in your environment before starting WikiBot.")
        raw_id = os.environ.get("DISCORD_GUILD_ID", "").strip()
        if raw_id and (not raw_id.isascii() or not raw_id.isdecimal() or int(raw_id) <= 0):
            raise ValueError("DISCORD_GUILD_ID must be a positive numeric server ID.")
        user_agent = os.environ.get("WIKIPEDIA_USER_AGENT", USER_AGENT).strip()
        if not user_agent or any(ord(c) < 32 or ord(c) > 126 for c in user_agent):
            raise ValueError("WIKIPEDIA_USER_AGENT must be a nonempty ASCII HTTP header.")
        return cls(token, int(raw_id) if raw_id else None, user_agent)


def safe_text(value: str, limit: int) -> str:
    value = discord.utils.escape_markdown(discord.utils.escape_mentions(value))
    # Discord limits are UTF-16 code units, not Python Unicode code points.
    encoded = value.encode("utf-16-le")
    if len(encoded) // 2 > limit:
        return encoded[: (limit - 1) * 2].decode("utf-16-le", errors="ignore") + "…"
    return value


def article_embed(article: Article) -> discord.Embed:
    description = article.extract or "No summary is available. Open the article to read more."
    if article.disambiguation:
        description = (
            "This topic has several meanings. Open the page or try a more specific title.\n\n"
            + description
        )
    embed = discord.Embed(
        title=safe_text(article.title, 256),
        description=safe_text(description, 3900),
        url=article.url,
        color=0xE91E63,
    )
    embed.set_footer(text="Source: English Wikipedia • CC BY-SA • See article for attribution")
    return embed


async def send_error(interaction: discord.Interaction, message: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(
            message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )
    else:
        await interaction.response.send_message(
            message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )


class WikiBot(discord.Client):
    def __init__(self, settings: Settings):
        super().__init__(
            intents=discord.Intents.none(), allowed_mentions=discord.AllowedMentions.none()
        )
        self.settings = settings
        self.session: aiohttp.ClientSession | None = None
        self.wikipedia: Wikipedia | None = None
        self.tree = app_commands.CommandTree(self)
        self.tree.on_error = self.on_tree_error
        self.tree.add_command(wiki)
        self.tree.add_command(help_command)
        self.tree.add_command(ping)

    async def setup_hook(self) -> None:
        self.session = aiohttp.ClientSession(
            headers={"User-Agent": self.settings.user_agent},
            connector=aiohttp.TCPConnector(limit=2),
        )
        self.wikipedia = Wikipedia(self.session)
        try:
            guild = discord.Object(id=self.settings.guild_id) if self.settings.guild_id else None
            if guild:
                self.tree.copy_global_to(guild=guild)
            commands = await self.tree.sync(guild=guild)
            log.info("Registered %d slash commands", len(commands))
        except Exception:
            await self.session.close()
            raise

    async def close(self) -> None:
        try:
            if self.session:
                await self.session.close()
        finally:
            await super().close()

    async def on_tree_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.CommandOnCooldown):
            message = f"Please wait {error.retry_after:.0f} seconds before another lookup."
        else:
            # Do not log interaction tokens, request headers or raw user topics.
            log.error("Slash command failed (%s)", type(error).__name__)
            message = "Something went wrong. Please try again later."
        await send_error(interaction, message)


@app_commands.command(name="wiki", description="Find a Wikipedia article and read its introduction")
@app_commands.describe(arg="Article title or search topic", private="Show the result only to you")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.checks.cooldown(1, 5.0, key=lambda interaction: interaction.user.id)
async def wiki(
    interaction: discord.Interaction, arg: app_commands.Range[str, 1, 200], private: bool = False
) -> None:
    try:
        query = validate_query(arg)
    except ValueError as error:
        await send_error(interaction, str(error))
        return
    # Acknowledge before network I/O to meet Discord's three-second deadline.
    await interaction.response.defer(thinking=True, ephemeral=private)
    client = interaction.client
    assert isinstance(client, WikiBot)
    try:
        if client.wikipedia is None:
            raise WikiError("WikiBot is still starting. Please try again shortly.")
        article = await client.wikipedia.lookup(query)
    except (WikiError, TimeoutError) as error:
        # Editing the deferred response also clears the loading indicator.
        message = (
            str(error)
            if isinstance(error, WikiError)
            else "Wikipedia is busy. Please try again later."
        )
        await interaction.edit_original_response(
            content=message, allowed_mentions=discord.AllowedMentions.none()
        )
        return
    if not interaction.app_permissions.embed_links:
        await interaction.edit_original_response(
            content=f"{safe_text(article.title, 256)}\n{article.url}",
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="Read on Wikipedia", url=article.url))
    await interaction.edit_original_response(
        embed=article_embed(article),
        view=view,
        allowed_mentions=discord.AllowedMentions.none(),
    )


@app_commands.command(name="help", description="Learn how to use WikiBot")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def help_command(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(
        "Use /wiki arg:<topic> for an English Wikipedia introduction. Exact titles and redirects "
        "are tried first, then search. Add private:true for a result only you can see. "
        "For ambiguous topics, use a more specific title. /ping checks the connection. "
        "Wikipedia content is community-written; verify important facts in the article's sources.",
        ephemeral=True,
    )


@app_commands.command(name="ping", description="Check WikiBot's Discord connection")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def ping(interaction: discord.Interaction) -> None:
    await interaction.response.send_message("WikiBot is online.", ephemeral=True)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    try:
        settings = Settings.from_env()
    except ValueError as error:
        raise SystemExit(str(error)) from None
    WikiBot(settings).run(settings.token)
