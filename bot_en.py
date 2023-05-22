import discord
from discord import app_commands
from discord.ext import commands
import requests
import json


def get_token():
    try:
        with open("token.json") as f:
            return json.load(f)["token"]
    except FileNotFoundError:
        token = input("Please enter your bot token: ")
        with open("token.json", "w") as f:
            json.dump({"token": token}, f)
        return token

bot = commands.Bot(command_prefix='/', intents=discord.Intents.all())


@bot.tree.command(name="wiki") 
@app_commands.describe(arg = "What to search")
async def wikien(interaction: discord.Interaction, arg: str):
    """Search Wikipedia and return the summary of the first matching article"""
    wikipedia_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{arg}"
    response = requests.get(wikipedia_url)
    data = response.json()
    summary = data.get("extract")
    if summary:
        embed = discord.Embed(title=f"Search result: {arg}", description=summary, color=0xe91e63)
        await interaction.response.send_message(embed=embed)
    else:
        embed2 = discord.Embed(title=f"Error", description="That wikipedia article does not exist.", color=0xe91e63)
        await interaction.response.send_message(embed=embed2, ephemeral=True)
    
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        await ctx.reply("Incorrect command usage. Use /help for help.")

@bot.event
async def on_ready():
    activity = discord.Activity(name='/wiki', type=discord.ActivityType.listening)
    await bot.change_presence(activity=activity)
    print("WikiBot\n")
    print("Bot is ready...")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(e)

bot.run(get_token())
