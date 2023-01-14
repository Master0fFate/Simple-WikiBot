import discord
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

@bot.command()
async def wiki(ctx, *, search):
    """Search Wikipedia and return the summary of the first matching article"""
    wikipedia_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{search}"
    response = requests.get(wikipedia_url)
    data = response.json()
    summary = data.get("extract")
    if summary:
        embed = discord.Embed(title=f"Search result of: {search}", description=summary, color=0xffffff)
        await ctx.reply(embed=embed)
    else:
        embed2 = discord.Embed(title=f"Error", description="That wikipedia page does not exist.", color=0xffffff)
        await ctx.reply(embed=embed2)
    
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        await ctx.reply("Incorrect syntax. Use '/wiki [search]'")

@bot.event
async def on_ready():
    activity = discord.Activity(name='/wiki', type=discord.ActivityType.listening)
    await bot.change_presence(activity=activity)
    print("WikiBot by github.com/Master0fFate\n")
    print("Bot is ready...")

bot.run(get_token())
