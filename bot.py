import os
import sqlite3
import random
import threading
import discord
from discord import app_commands
from discord.ext import commands, tasks
from google import genai
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- CONFIGURATION ---
TOKEN = os.environ.get("DISCORD_TOKEN")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
FORGE_HEX_COLOR = discord.Color(int("1E1F22", 16))
LEVEL_UP_CHANNEL_ID = 1487499108595011798 
LEADERBOARD_CHANNEL_ID = 1527999005471146084 

LEVEL_ROLES = {
    1: 1513178112412618762,
    5: 1471798637854982264,
    10: 1471798683938062489,
    20: 1513178466718187600,
    35: 1513178572091817995,
    50: 1513179470322995270
}

# --- KOYEB PING KEEP-ALIVE SERVER ---
# This forces Koyeb to see the bot as an active web service so it never sleeps!
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(b"Forge Bot is Alive!")

def run_health_server():
    # Koyeb passes a port via environment variables automatically
    port = int(os.environ.get("PORT", 8000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

# --- DATABASE SETUP ---
conn = sqlite3.connect("levels.db")
cursor = conn.cursor()
cursor.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, xp INTEGER DEFAULT 0, level INTEGER DEFAULT 0)")
cursor.execute("CREATE TABLE IF NOT EXISTS system_config (key TEXT PRIMARY KEY, value TEXT)")
conn.commit()

# --- BOT INITIALIZATION ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)
ai_client = genai.Client(api_key=GEMINI_KEY)

def get_xp_needed(level):
    return 100 * (level ** 2) + 100

def add_user_xp(user_id, xp_to_add):
    cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    cursor.execute("SELECT xp, level FROM users WHERE user_id = ?", (user_id,))
    xp, level = cursor.fetchone()
    new_xp = xp + xp_to_add
    leveled_up = False
    while new_xp >= get_xp_needed(level + 1):
        level += 1
        leveled_up = True
    cursor.execute("UPDATE users SET xp = ?, level = ? WHERE user_id = ?", (new_xp, level, user_id))
    conn.commit()
    return leveled_up, level

@tasks.loop(seconds=60)
async def refresh_leaderboard():
    channel = bot.get_channel(LEADERBOARD_CHANNEL_ID)
    if not channel: return
    cursor.execute("SELECT user_id, level, xp FROM users ORDER BY level DESC, xp DESC LIMIT 10")
    top_users = cursor.fetchall()
    embed = discord.Embed(title="Forge: Tower Defense - Leaderboard", description="*Updates automatically every minute*\n\n", color=FORGE_HEX_COLOR)
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    leaderboard_text = ""
    for index, row in enumerate(top_users):
        user_id, level, xp = row
        leaderboard_text += f"{medals[index]} <@{user_id}> • **Level {level}** ({xp} XP)\n"
    if not leaderboard_text: leaderboard_text = "*No data recorded yet.*"
    embed.description += leaderboard_text
    cursor.execute("SELECT value FROM system_config WHERE key = 'leaderboard_msg_id'")
    msg_id_row = cursor.fetchone()
    if msg_id_row:
        try:
            message = await channel.fetch_message(int(msg_id_row[0]))
            await message.edit(embed=embed)
            return
        except: pass
    new_msg = await channel.send(embed=embed)
    cursor.execute("INSERT OR REPLACE INTO system_config (key, value) VALUES ('leaderboard_msg_id', ?)", (str(new_msg.id),))
    conn.commit()

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name}")
    await bot.tree.sync()
    if not refresh_leaderboard.is_running():
        refresh_leaderboard.start()

@bot.event
async def on_message(message):
    if message.author.bot: return
    if bot.user.mentioned_in(message):
        clean_text = message.content.replace(f"<@{bot.user.id}>", "").strip()
        async with message.channel.typing():
            try:
                response = ai_client.models.generate_content(model='gemini-2.5-flash', contents=clean_text, config={'system_instruction': "Forge TD Bot assistant vibe."})
                await message.reply(response.text)
            except:
                await message.reply("Gears jammed!")
        return
    leveled_up, new_level = add_user_xp(message.author.id, random.randint(15, 25))
    if leveled_up:
        announcement_channel = bot.get_channel(LEVEL_UP_CHANNEL_ID)
        if announcement_channel:
            embed = discord.Embed(title="Level Up", description=f"**{message.author.mention}** hit **Level {new_level}**!", color=FORGE_HEX_COLOR)
            if new_level in LEVEL_ROLES:
                role = message.guild.get_role(LEVEL_ROLES[new_level])
                if role:
                    try: await message.author.add_roles(role)
                    except: pass
            await announcement_channel.send(embed=embed)
    await bot.process_commands(message)

@bot.tree.command(name="rank")
async def rank(interaction: discord.Interaction, member: discord.Member = None):
    target = member or interaction.user
    cursor.execute("SELECT xp, level FROM users WHERE user_id = ?", (target.id,))
    res = cursor.fetchone()
    if not res:
        await interaction.response.send_message("No chat history!", ephemeral=True)
        return
    xp, level = res
    embed = discord.Embed(title=f"📊 {target.display_name}'s Rank", color=FORGE_HEX_COLOR)
    embed.add_field(name="Level", value=str(level))
    embed.add_field(name="XP", value=f"{xp} / {get_xp_needed(level + 1)}")
    await interaction.response.send_message(embed=embed)

# Start health check server in background thread, then launch bot
threading.Thread(target=run_health_server, daemon=True).start()
bot.run(TOKEN)
