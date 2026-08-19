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
STAFF_CHANNEL_ID = int(os.getenv("STAFF_CHANNEL_ID", "0"))


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

alerted_bans: set[str] = set()
alerted_down: set[str] = set()
alerted_group_locked: set[str] = set()

cached_games: list = []
cached_groups: list = []


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
        if removed and (name == "[TITLE UNAVAILABLE]" or not name):
            name = f"Game {universe_id}"
        creator = dev_data.get("creator") or {}
        holder_id = creator.get("id")
        return name, status, players, link, holder_id
    except Exception as e:
        print(f"Error fetching game {universe_id}: {e}")
        return f"Game {universe_id}", False, 0, None, None


async def get_group_data(session, group_id):
    url = f"https://groups.roblox.com/v1/groups/{group_id}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            data = await resp.json()
        name = data.get("name", f"Group {group_id}")
        member_count = data.get("memberCount", 0)
        is_locked = data.get("isLocked", False)
        owner = data.get("owner") or {}
        holder_id = owner.get("userId")
        return name, member_count, is_locked, holder_id
    except Exception as e:
        print(f"Error fetching group {group_id}: {e}")
        return f"Group {group_id}", 0, False, None


async def check_user_banned(session, user_id) -> tuple[bool, str]:
    if not user_id:
        return False, "unknown"
    url = f"https://users.roblox.com/v1/users/{user_id}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 404:
                return True, f"user_{user_id}"
            data = await resp.json()
        username = data.get("name", f"user_{user_id}")
        is_banned = data.get("isBanned", False)
        return is_banned, username
    except Exception as e:
        print(f"Error checking ban for user {user_id}: {e}")
        return False, f"user_{user_id}"


async def send_ban_alert(entity_name: str, entity_type: str, holder_id: int, username: str):
    staff_channel = client.get_channel(STAFF_CHANNEL_ID)
    if not staff_channel:
        return
    profile = f"https://www.roblox.com/users/{holder_id}/profile"
    await staff_channel.send(
        f"**holder banned 🔨⚠️**\n"
        f"> **{entity_type}:** [{entity_name}](<{profile}>)\n"
        f"> **Holder:** {username} (`{holder_id}`)\n"
        f"> **Time:** <t:{int(time.time())}:F>\n"
        f"||@everyone||"
    )


async def send_down_alert(game_name: str, universe_id: str):
    staff_channel = client.get_channel(STAFF_CHANNEL_ID)
    if not staff_channel:
        return
    root_place = None
    for uid, (name, status, players, link, holder_id) in zip(UNIVERSE_IDS, cached_games):
        if uid == universe_id:
            root_place = link
            break
    game_link = root_place or f"https://www.roblox.com/games/{universe_id}"
    await staff_channel.send(
        f"**game deleted 🔴⚠️**\n"
        f"> **Game:** [{game_name}](<{game_link}>)\n"
        f"> **Time:** <t:{int(time.time())}:F>\n"
        f"||@everyone||"
    )


async def send_group_locked_alert(group_name: str, group_id: str):
    staff_channel = client.get_channel(STAFF_CHANNEL_ID)
    if not staff_channel:
        return
    group_link = f"https://www.roblox.com/groups/{group_id}"
    await staff_channel.send(
        f"**group locked 🔒⚠️**\n"
        f"> **Group:** [{group_name}](<{group_link}>)\n"
        f"> **Time:** <t:{int(time.time())}:F>\n"
        f"||@everyone||"
    )


def build_message_from_cache() -> list[str]:
    combined = list(zip(UNIVERSE_IDS, cached_games))
    combined.sort(key=lambda x: x[1][2], reverse=True)
    total_online = sum(r[2] for _, r in combined)

    games_header = "OUR GAMES" if len(combined) > 1 else "OUR GAME"
    lines = [f"## **{games_header} **"]

    for uid, (name, status, players, link, holder_id) in combined:
        icon = "🟢" if status else "🔴"
        status_text = "Active" if status else "Down"
        game_link = link or f"https://www.roblox.com/games/{uid}"
        lines.append(
            f"***{name}***\n"
            f"> -# Game Status: {status_text} {icon}\n"
            f"> -# Online: {players} 👥\n"
            f"[__**JOIN GAME**__](<{game_link}>) \n"
        )

    lines.append(f"-# **Total Online: {total_online}** 👥")

    valid_groups = [(gid, g) for gid, g in zip(GROUP_IDS, cached_groups) if not g[2]]
    if valid_groups:
        groups_header = "OUR GROUPS" if len(valid_groups) > 1 else "OUR GROUP"
        total_members = sum(g[1] for _, g in valid_groups)
        lines.append(f"## **{groups_header}**")

        for gid, (group_name, member_count, is_locked, holder_id) in valid_groups:
            group_link = f"https://www.roblox.com/groups/{gid}"
            lines.append(
                f"***{group_name}***\n"
                f"> -# Members: {member_count:,} 👥\n"
                f"[__**JOIN GROUP**__](<{group_link}>)\n"
            )

        lines.append(f"-# **Total Members: {total_members:,}** 👥")

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


@tasks.loop(seconds=300)
async def check_status():
    global cached_games, cached_groups

    async with aiohttp.ClientSession() as session:
        games_results, groups_results = await asyncio.gather(
            asyncio.gather(*[get_game_full_data(session, uid) for uid in UNIVERSE_IDS]),
            asyncio.gather(*[get_group_data(session, gid) for gid in GROUP_IDS]),
        )

        cached_games = list(games_results)
        cached_groups = list(groups_results)

        for uid, (name, status, players, link, holder_id) in zip(UNIVERSE_IDS, cached_games):
            if not status and uid not in alerted_down:
                alerted_down.add(uid)
                await send_down_alert(name, uid)
            elif status and uid in alerted_down:
                alerted_down.discard(uid)

        for gid, (g_name, member_count, is_locked, holder_id) in zip(GROUP_IDS, cached_groups):
            if is_locked and gid not in alerted_group_locked:
                alerted_group_locked.add(gid)
                await send_group_locked_alert(g_name, gid)
            elif not is_locked and gid in alerted_group_locked:
                alerted_group_locked.discard(gid)

        checks = []
        for uid, (name, status, players, link, holder_id) in zip(UNIVERSE_IDS, cached_games):
            if holder_id:
                checks.append(("Game", name, uid, holder_id))
        for gid, (g_name, member_count, is_locked, holder_id) in zip(GROUP_IDS, cached_groups):
            if holder_id:
                checks.append(("Group", g_name, gid, holder_id))

        ban_results = await asyncio.gather(*[check_user_banned(session, c[3]) for c in checks])

        for (entity_type, entity_name, entity_id, holder_id), (is_banned, username) in zip(checks, ban_results):
            alert_key = f"{entity_type}:{entity_id}:{holder_id}"
            if is_banned and alert_key not in alerted_bans:
                alerted_bans.add(alert_key)
                await send_ban_alert(entity_name, entity_type, holder_id, username)
            elif not is_banned:
                alerted_bans.discard(alert_key)


@tasks.loop(seconds=1800)
async def update_message():
    global message_ids
    if not cached_games:
        return

    channel = client.get_channel(CHANNEL_ID)
    if not channel:
        return

    chunks = build_message_from_cache()

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


@check_status.before_loop
async def before_check():
    await client.wait_until_ready()

@update_message.before_loop
async def before_update():
    await client.wait_until_ready()
    await check_status()

@client.event
async def on_ready():
    print(f"Bot started as {client.user}")
    check_status.start()
    update_message.start()


client.run(DISCORD_TOKEN)
