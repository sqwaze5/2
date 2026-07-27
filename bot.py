import os
import time
import asyncio
import aiohttp
import discord
from discord.ext import tasks
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))

UNIVERSE_IDS = []
i = 1
while True:
    uid = os.getenv(f"UNIVERSE_ID_{i}", "")
    if not uid:
        break
    UNIVERSE_IDS.append(uid)
    i += 1

GROUP_IDS = []
i = 1
while True:
    gid = os.getenv(f"GROUP_ID_{i}", "")
    if not gid:
        break
    GROUP_IDS.append(gid)
    i += 1
if not GROUP_IDS:
    legacy = os.getenv("GROUP_ID", "")
    if legacy:
        GROUP_IDS.append(legacy)

intents = discord.Intents.default()
client = discord.Client(intents=intents)
message_ids = []


def is_place_removed(game_entry):
    if not game_entry:
        return False
    if game_entry.get("id", 0) == 0:
        return True
    if game_entry.get("name") == "[TITLE UNAVAILABLE]":
        return True
    creator = game_entry.get("creator") or {}
    if creator.get("id", 0) == 0 and creator.get("name") == "[UNKNOWN]":
        return True
    created = game_entry.get("created", "")
    if created.startswith("0001-01-01"):
        return True
    return False


async def get_game_full_data(session, universe_id):
    dev_url = f"https://develop.roblox.com/v1/universes/{universe_id}"
    game_url = f"https://games.roblox.com/v1/games?universeIds={universe_id}"
    try:
        async with session.get(dev_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            dev_data = await resp.json()
        async with session.get(game_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            game_data = await resp.json()

        name = dev_data.get("name", f"Game {universe_id}")
        root_place = dev_data.get("rootPlaceId")
        link = f"https://www.roblox.com/games/{root_place}" if root_place else None

        is_active = dev_data.get("isActive", False)
        privacy = dev_data.get("privacyType", "Private")
        has_game_data = bool(game_data.get("data"))

        entry = game_data["data"][0] if has_game_data else None
        removed = is_place_removed(entry)

        status = is_active and privacy == "Public" and has_game_data and not removed

        players = 0
        if has_game_data and not removed:
            players = entry.get("playing", 0)

        if removed:
            if name == "[TITLE UNAVAILABLE]" or not name:
                name = f"Game {universe_id}"

        return name, status, players, link
    except Exception as e:
        print(f"Error fetching game {universe_id}: {e}")
        return f"Game {universe_id}", False, 0, None


async def get_group_data(session, group_id):
    url = f"https://groups.roblox.com/v1/groups/{group_id}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            data = await resp.json()
        name = data.get("name", f"Group {group_id}")
        member_count = data.get("memberCount", 0)
        is_locked = data.get("isLocked", False)
        return name, member_count, is_locked
    except Exception as e:
        print(f"Error fetching group {group_id}: {e}")
        return f"Group {group_id}", 0, False


async def build_message():
    now = int(time.time())
    async with aiohttp.ClientSession() as session:
        games_results, groups_results = await asyncio.gather(
            asyncio.gather(*[get_game_full_data(session, uid) for uid in UNIVERSE_IDS]),
            asyncio.gather(*[get_group_data(session, gid) for gid in GROUP_IDS]),
        )

    combined = list(zip(UNIVERSE_IDS, games_results))
    combined.sort(key=lambda x: x[1][2], reverse=True)

    total_online = sum(result[2] for _, result in combined)

    lines = []
    lines.append("## ** OUR GAMES **")
    for uid, (name, status, players, link) in combined:
        status_text = "Active" if status else "Down"
        icon = "🟢" if status else "🔴"
        block = (
            f"**{name}**\n"
            f"> * Game Status: {status_text} {icon}\n"
            f"> * Online: {players} 👥\n"
            f"[JOIN GAME](<{link}>) 👈\n"
        )
        lines.append(block)

    lines.append(f"**Total Online: {total_online} 👥**\n")

    group_lines = []
    for gid, (group_name, member_count, is_locked) in zip(GROUP_IDS, groups_results):
        if not is_locked:
            group_link = f"https://www.roblox.com/groups/{gid}"
            group_lines.append(
                f"**{group_name}**\n"
                f"> * Members: {member_count:,} 👥\n"
                f"[JOIN GROUP](<{group_link}>) 👈\n"
            )

    if group_lines:
        lines.append("## \n** OUR GROUPS **")
        lines.extend(group_lines)

    lines.append(f"\n⏱ Last Update: <t:{now}:R>")

    content = "\n".join(lines)
    chunks = []
    while len(content) > 2000:
        split_at = content.rfind("\n", 0, 2000)
        if split_at == -1:
            split_at = 2000
        chunks.append(content[:split_at])
        content = content[split_at:].lstrip("\n")
    chunks.append(content)
    return chunks


@tasks.loop(seconds=1800)
async def update_status():
    global message_ids
    channel = client.get_channel(CHANNEL_ID)
    if not channel:
        return

    chunks = await build_message()

    try:
        if not message_ids:
            for chunk in chunks:
                msg = await channel.send(chunk)
                message_ids.append(msg.id)
        else:
            for i, chunk in enumerate(chunks):
                if i < len(message_ids):
                    try:
                        msg = await channel.fetch_message(message_ids[i])
                        await msg.edit(content=chunk)
                    except discord.NotFound:
                        msg = await channel.send(chunk)
                        message_ids[i] = msg.id
                else:
                    msg = await channel.send(chunk)
                    message_ids.append(msg.id)
    except Exception as e:
        print(f"Error updating message: {e}")


@client.event
async def on_ready():
    print(f"Bot started as {client.user}")
    update_status.start()


client.run(DISCORD_TOKEN)
