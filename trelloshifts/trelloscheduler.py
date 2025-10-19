import discord
from discord.ext import commands
import aiohttp
import asyncio
from datetime import datetime
import pytz
from core import checks
from core.checks import PermissionLevel

local_tz = pytz.timezone("America/Chicago")

class ScheduleSessionModal(discord.ui.Modal):
    def __init__(self, bot, plugin_db, session_type, trello_cfg, logs_channel_id):
        super().__init__(title="Schedule Session")
        self.bot = bot
        self.db = plugin_db
        self.session_type = session_type
        self.trello = trello_cfg
        self.logs_channel_id = logs_channel_id
        self.host = discord.ui.TextInput(label="Host (Roblox username)", style=discord.TextStyle.short, required=True, max_length=100)
        self.cohost = discord.ui.TextInput(label="Cohost (optional)", style=discord.TextStyle.short, required=False, max_length=100)
        self.description = discord.ui.TextInput(label="Description", style=discord.TextStyle.long, required=True, max_length=2000)
        self.date = discord.ui.TextInput(label="Date (MM/DD/YYYY)", style=discord.TextStyle.short, required=True, placeholder="06/15/2025")
        self.time = discord.ui.TextInput(label="Time (24h, HH:MM)", style=discord.TextStyle.short, required=True, placeholder="14:30")
        self.add_item(self.host)
        self.add_item(self.cohost)
        self.add_item(self.description)
        self.add_item(self.date)
        self.add_item(self.time)

    async def parse_datetime(self, date_str, time_str):
        try:
            dt = datetime.strptime(f"{date_str} {time_str}", "%m/%d/%Y %H:%M")
            localized = local_tz.localize(dt)
            iso_utc = localized.astimezone(pytz.utc).isoformat()
            return iso_utc, localized.isoformat()
        except Exception:
            return None, None

    async def create_or_get_label(self, session, board_id, label_name):
        async with session.get(f"https://api.trello.com/1/boards/{board_id}/labels", params={"key": self.trello["PersonalKey"], "token": self.trello["Token"], "limit": 1000}) as r:
            if r.status == 200:
                labels = await r.json()
                for lab in labels:
                    if lab.get("name","").lower() == label_name.lower():
                        return lab.get("id")
        async with session.post("https://api.trello.com/1/labels", params={"key": self.trello["PersonalKey"], "token": self.trello["Token"]}, json={"idBoard": board_id, "name": label_name, "color": None}) as r2:
            if r2.status in (200,201):
                lab = await r2.json()
                return lab.get("id")
        return None

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=False)
        host_val = self.host.value.strip()
        cohost_val = self.cohost.value.strip()
        desc_val = self.description.value.strip()
        date_val = self.date.value.strip()
        time_val = self.time.value.strip()
        iso_utc, iso_local = await self.parse_datetime(date_val, time_val)
        desc_lines = []
        desc_lines.append(f"Host: {host_val}")
        desc_lines.append(f"Cohost: {cohost_val if cohost_val else 'None'}")
        desc_lines.append(f"Description: {desc_val}")
        desc_lines.append(f"Date (as entered): {date_val}")
        desc_lines.append(f"Time (as entered): {time_val} (24h)")
        if iso_local:
            desc_lines.append(f"Parsed local ISO: {iso_local}")
        if iso_utc:
            desc_lines.append(f"Parsed UTC ISO: {iso_utc}")
        full_desc = "\n".join(desc_lines)
        title_map = {
            "shift": "Shift",
            "training": "Training Session",
            "largeshift": "LARGE SHIFT"
        }
        card_title = title_map.get(self.session_type.lower(), self.session_type)
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://api.trello.com/1/lists/{self.trello['ListId']}", params={"key": self.trello["PersonalKey"], "token": self.trello["Token"], "fields": "idBoard"}) as r:
                if r.status != 200:
                    await interaction.followup.send("Failed to access Trello list.", ephemeral=True)
                    return
                list_info = await r.json()
                board_id = list_info.get("idBoard")
            scheduled_label_id = await self.create_or_get_label(session, board_id, "Scheduled")
            payload = {"name": card_title, "desc": full_desc, "idList": self.trello["ListId"], "key": self.trello["PersonalKey"], "token": self.trello["Token"]}
            if scheduled_label_id:
                payload["idLabels"] = scheduled_label_id
            async with session.post("https://api.trello.com/1/cards", params=payload) as cr:
                if cr.status not in (200,201):
                    text = await cr.text()
                    await interaction.followup.send(f"Failed to create Trello card: {cr.status} {text}", ephemeral=True)
                    return
                card = await cr.json()
                card_url = card.get("shortUrl") or card.get("url")
                db_entry = {"card_id": card.get("id"), "shortLink": card.get("shortLink"), "card_url": card_url, "session_type": self.session_type, "host": host_val, "cohost": cohost_val, "description": desc_val, "date": date_val, "time": time_val, "created_by": str(interaction.user.id), "created_at": datetime.utcnow().isoformat()}
                await self.db.insert_one(db_entry)
                await interaction.followup.send(f"Created Trello card: {card_url}")
                if self.logs_channel_id:
                    logs_chan = self.bot.get_channel(int(self.logs_channel_id))
                    if logs_chan:
                        await logs_chan.send(f"{interaction.user.mention} created Trello card {card_url} for session `{self.session_type}`")

class TrelloScheduler(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = self.bot.plugin_db.get_partition(self)
        self.trelloConfig = {
            "ListId": "68f444860b7854a2fef52fa4",
            "PersonalKey": "ac3c79179852faa3868698ec07b41594",
            "Token": "ATTA00e7c47e3440690fe364137898a081e282cd8ca8aa733f791e5fedf780ba6b7314764152"
        }

    @commands.command(name="schedulesession")
    async def schedulesession(self, ctx, session_type: str):
        logs_entry = await self.db.find_one({"_id": "logs_channel"})
        logs_channel_id = logs_entry["channel_id"] if logs_entry else None
        modal = ScheduleSessionModal(self.bot, self.db, session_type, self.trelloConfig, logs_channel_id)
        await ctx.send_modal(modal)

    @commands.command(name="cancelshift")
    async def cancelshift(self, ctx, card_id_or_url: str = None):
        if not card_id_or_url:
            await ctx.send("Provide Trello card ID or URL to cancel.")
            return
        card_id = card_id_or_url.split("/")[-1] if "trello.com" in card_id_or_url else card_id_or_url
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://api.trello.com/1/cards/{card_id}", params={"key": self.trelloConfig["PersonalKey"], "token": self.trelloConfig["Token"]}) as r:
                if r.status != 200:
                    await ctx.send("Could not find Trello card.")
                    return
                card = await r.json()
                board_id = card.get("idBoard")
            cancelled_label_id = None
            async with aiohttp.ClientSession() as session2:
                async with session2.get(f"https://api.trello.com/1/boards/{board_id}/labels", params={"key": self.trelloConfig["PersonalKey"], "token": self.trelloConfig["Token"], "limit": 1000}) as rl:
                    if rl.status == 200:
                        labs = await rl.json()
                        for l in labs:
                            if l.get("name","").lower() == "cancelled":
                                cancelled_label_id = l.get("id")
                                break
                if not cancelled_label_id:
                    async with session2.post("https://api.trello.com/1/labels", params={"key": self.trelloConfig["PersonalKey"], "token": self.trelloConfig["Token"]}, json={"idBoard": board_id, "name": "Cancelled", "color": None}) as rc:
                        if rc.status in (200,201):
                            lab = await rc.json()
                            cancelled_label_id = lab.get("id")
                if not cancelled_label_id:
                    await ctx.send("Failed to create/find 'Cancelled' label.")
                    return
                async with session2.post(f"https://api.trello.com/1/cards/{card_id}/idLabels", params={"key": self.trelloConfig["PersonalKey"], "token": self.trelloConfig["Token"]}, json={"value": cancelled_label_id}) as addl:
                    if addl.status in (200,201):
                        await ctx.send("Added 'Cancelled' label to card.")
                        logs_entry = await self.db.find_one({"_id": "logs_channel"})
                        if logs_entry:
                            ch = self.bot.get_channel(int(logs_entry["channel_id"]))
                            if ch:
                                await ch.send(f"{ctx.author.mention} added 'Cancelled' label to {card.get('shortUrl') or card.get('url')}")
                    else:
                        txt = await addl.text()
                        await ctx.send(f"Failed to add label: {addl.status} {txt}")

    @checks.has_permissions(PermissionLevel.MODERATOR)
    @commands.command(name="setlogs")
    async def setlogs(self, ctx, channel: discord.TextChannel):
        await self.db.find_one_and_update({"_id": "logs_channel"}, {"$set": {"channel_id": str(channel.id)}}, upsert=True)
        await ctx.send(f"Logs channel set to {channel.mention}")

async def setup(bot):
    await bot.add_cog(TrelloScheduler(bot))
