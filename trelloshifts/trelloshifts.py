"""
TrelloScheduler Cog

Usage notes:
- Put Trello credentials into the plugin DB partition for this cog under the document
  with _id == "trello_config", for example:
  {
    "_id": "trello_config",
    "ListId": "your_list_id",
    "PersonalKey": "your_key",
    "Token": "your_token"
  }

- Logs channel should be set with the "setlogs" command (it stores {"_id": "logs_channel", "channel_id": "<id>"}).
"""

import discord
from discord.ext import commands
import aiohttp
from datetime import datetime
import pytz
import logging

from core import checks
from core.checks import PermissionLevel

logger = logging.getLogger(__name__)
local_tz = pytz.timezone("America/Chicago")


class ScheduleSessionModal(discord.ui.Modal):
    def __init__(self, bot, plugin_db, session_type: str, trello_cfg: dict, logs_channel_id):
        super().__init__(title="Schedule Session")
        self.bot = bot
        self.db = plugin_db
        self.session_type = session_type
        self.trello = trello_cfg or {}
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

    async def parse_datetime(self, date_str: str, time_str: str):
        try:
            dt = datetime.strptime(f"{date_str} {time_str}", "%m/%d/%Y %H:%M")
            localized = local_tz.localize(dt)
            iso_utc = localized.astimezone(pytz.utc).isoformat()
            return iso_utc, localized.isoformat()
        except Exception as e:
            logger.debug("parse_datetime failed: %s", e)
            return None, None

    async def create_or_get_label(self, session: aiohttp.ClientSession, board_id: str, label_name: str, color: str = None):
        """Return label id for name (create if missing)."""
        # Get existing labels
        params = {"key": self.trello.get("PersonalKey"), "token": self.trello.get("Token"), "limit": 1000}
        async with session.get(f"https://api.trello.com/1/boards/{board_id}/labels", params=params) as r:
            if r.status == 200:
                labels = await r.json()
                for lab in labels:
                    if lab.get("name", "").lower() == label_name.lower():
                        return lab.get("id")

        # Create label
        post_params = {"key": self.trello.get("PersonalKey"), "token": self.trello.get("Token")}
        json_body = {"idBoard": board_id, "name": label_name}
        if color:
            json_body["color"] = color
        async with session.post("https://api.trello.com/1/labels", params=post_params, json=json_body) as r2:
            if r2.status in (200, 201):
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
        if not iso_utc:
            await interaction.followup.send("Invalid date or time format. Please use `MM/DD/YYYY` and `HH:MM` (24h).", ephemeral=True)
            return

        desc_lines = [
            f"Host: {host_val}",
            f"Cohost: {cohost_val if cohost_val else 'None'}",
            f"Description: {desc_val}",
            "---",
            f"Date Entered: {date_val}",
            f"Time Entered: {time_val} (America/Chicago)"
        ]
        full_desc = "\n".join(desc_lines)

        title_map = {
            "shift": "Shift",
            "training": "Training Session",
            "largeshift": "LARGE SHIFT"
        }
        card_title = title_map.get(self.session_type.lower(), self.session_type.capitalize())

        # Basic validation for Trello config
        list_id = self.trello.get("ListId")
        if not list_id or not self.trello.get("PersonalKey") or not self.trello.get("Token"):
            await interaction.followup.send("Trello configuration is missing. Contact a bot administrator.", ephemeral=True)
            return

        async with aiohttp.ClientSession() as session:
            try:
                # Fetch board id from list
                params = {"key": self.trello["PersonalKey"], "token": self.trello["Token"], "fields": "idBoard"}
                async with session.get(f"https://api.trello.com/1/lists/{list_id}", params=params) as r:
                    if r.status != 200:
                        text = await r.text()
                        logger.warning("Failed to get Trello list: %s %s", r.status, text)
                        await interaction.followup.send("Failed to access Trello list.", ephemeral=True)
                        return
                    list_info = await r.json()
                    board_id = list_info.get("idBoard")

                scheduled_label_id = await self.create_or_get_label(session, board_id, "Scheduled", "green")

                payload = {
                    "name": card_title,
                    "desc": full_desc,
                    "idList": list_id,
                    "due": iso_utc,
                    "key": self.trello["PersonalKey"],
                    "token": self.trello["Token"]
                }

                # Trello expects idLabels as comma-separated string or list — send as list for clarity
                if scheduled_label_id:
                    payload["idLabels"] = [scheduled_label_id]

                async with session.post("https://api.trello.com/1/cards", params=payload) as cr:
                    if cr.status not in (200, 201):
                        text = await cr.text()
                        logger.warning("Failed to create Trello card: %s %s", cr.status, text)
                        await interaction.followup.send(f"Failed to create Trello card: {cr.status} {text}", ephemeral=True)
                        return

                    card = await cr.json()
                    card_url = card.get("shortUrl") or card.get("url")

                    db_entry = {
                        "card_id": card.get("id"),
                        "shortLink": card.get("shortLink"),
                        "card_url": card_url,
                        "session_type": self.session_type,
                        "host": host_val,
                        "cohost": cohost_val,
                        "description": desc_val,
                        "date": date_val,
                        "time": time_val,
                        "created_by": str(interaction.user.id),
                        "created_at": datetime.utcnow().isoformat()
                    }
                    await self.db.insert_one(db_entry)

                    await interaction.followup.send(f"Created Trello card: {card_url}")

                    # Post log embed if logs channel present
                    if self.logs_channel_id:
                        logs_chan = self.bot.get_channel(int(self.logs_channel_id))
                        if logs_chan:
                            embed = discord.Embed(title="Session Scheduled", color=discord.Color.green(), url=card_url)
                            embed.add_field(name="Type", value=card_title, inline=False)
                            embed.add_field(name="Host", value=host_val, inline=True)
                            if cohost_val:
                                embed.add_field(name="Cohost", value=cohost_val, inline=True)
                            embed.add_field(name="Time", value=f"{date_val} at {time_val} (CST)", inline=False)
                            # safely get avatar url
                            avatar_url = getattr(interaction.user.display_avatar, "url", None) or getattr(interaction.user, "avatar_url", None)
                            embed.set_footer(text=f"Scheduled by {interaction.user}", icon_url=avatar_url)
                            await logs_chan.send(embed=embed)

            except Exception as e:
                logger.exception("Unexpected error creating Trello card")
                await interaction.followup.send(f"An error occurred: {e}", ephemeral=True)


class TrelloScheduler(commands.Cog):
    """Schedules sessions and posts them to Trello."""

    # You can set a default placeholder here. Prefer storing real secrets in plugin_db instead.
    _default_trello = {
        "ListId": None,
        "PersonalKey": None,
        "Token": None
    }

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = None  # set in cog_load
        self.trelloConfig = None
        self.logs_channel_id = None

    async def cog_load(self) -> None:
        """Load DB partition and Trello configuration at runtime (no import-time side-effects)."""
        # plugin_db partition
        self.db = self.bot.plugin_db.get_partition(self)

        # Load trello config from plugin DB document _id == "trello_config"
        cfg = await self.db.find_one({"_id": "trello_config"})
        if cfg:
            # do not overwrite other keys accidentally
            self.trelloConfig = {
                "ListId": cfg.get("ListId") or self._default_trello["ListId"],
                "PersonalKey": cfg.get("PersonalKey") or self._default_trello["PersonalKey"],
                "Token": cfg.get("Token") or self._default_trello["Token"]
            }
        else:
            self.trelloConfig = dict(self._default_trello)

        # Load logs channel id (same approach as your prior plugin)
        logs_entry = await self.db.find_one({"_id": "logs_channel"})
        self.logs_channel_id = logs_entry["channel_id"] if logs_entry else None

        logger.info("TrelloScheduler loaded. ListId=%s LogsChannel=%s", self.trelloConfig.get("ListId"), self.logs_channel_id)

    # -------------------------
    # Commands
    # -------------------------
    @commands.command(name="schedulesession")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def schedulesession(self, ctx: commands.Context, session_type: str):
        """Opens a modal to schedule a new session."""
        modal = ScheduleSessionModal(self.bot, self.db, session_type, self.trelloConfig, self.logs_channel_id)
        await ctx.send_modal(modal)

    @commands.command(name="cancelshift")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def cancelshift(self, ctx: commands.Context, card_id_or_url: str):
        """Cancels a shift by adding the 'Cancelled' label to its Trello card."""
        if not card_id_or_url:
            await ctx.send("Provide Trello card ID or URL to cancel.")
            return

        card_id = card_id_or_url.split("/")[-1] if "trello.com" in card_id_or_url else card_id_or_url

        # validate trello config
        if not self.trelloConfig or not self.trelloConfig.get("PersonalKey") or not self.trelloConfig.get("Token"):
            await ctx.send("Trello configuration missing. Contact a bot administrator.")
            return

        async with aiohttp.ClientSession() as session:
            try:
                # Get card
                params = {"key": self.trelloConfig["PersonalKey"], "token": self.trelloConfig["Token"]}
                async with session.get(f"https://api.trello.com/1/cards/{card_id}", params=params) as r:
                    if r.status != 200:
                        await ctx.send("Could not find Trello card.")
                        return
                    card = await r.json()
                    board_id = card.get("idBoard")

                # Find or create 'Cancelled' label on the board
                cancelled_label_id = None
                params = {"key": self.trelloConfig["PersonalKey"], "token": self.trelloConfig["Token"], "limit": 1000}
                async with session.get(f"https://api.trello.com/1/boards/{board_id}/labels", params=params) as rl:
                    if rl.status == 200:
                        labs = await rl.json()
                        for l in labs:
                            if l.get("name", "").lower() == "cancelled":
                                cancelled_label_id = l.get("id")
                                break

                if not cancelled_label_id:
                    post_params = {"key": self.trelloConfig["PersonalKey"], "token": self.trelloConfig["Token"]}
                    json_body = {"idBoard": board_id, "name": "Cancelled", "color": "red"}
                    async with session.post("https://api.trello.com/1/labels", params=post_params, json=json_body) as rc:
                        if rc.status in (200, 201):
                            lab = await rc.json()
                            cancelled_label_id = lab.get("id")
                        else:
                            text = await rc.text()
                            logger.warning("Failed to create 'Cancelled' label: %s %s", rc.status, text)
                            await ctx.send("Failed to create 'Cancelled' label.")
                            return

                # Add label to card
                post_params = {"key": self.trelloConfig["PersonalKey"], "token": self.trelloConfig["Token"]}
                async with session.post(f"https://api.trello.com/1/cards/{card_id}/idLabels", params=post_params, json={"value": cancelled_label_id}) as addl:
                    if addl.status in (200, 201):
                        await ctx.send("Added 'Cancelled' label to card.")

                        # optional logging to channel
                        logs_entry = await self.db.find_one({"_id": "logs_channel"})
                        if logs_entry:
                            ch = self.bot.get_channel(int(logs_entry["channel_id"]))
                            if ch:
                                embed = discord.Embed(title="Session Cancelled", color=discord.Color.red(), url=card.get('shortUrl') or card.get('url'))
                                embed.add_field(name="Card", value=card.get('name'))
                                avatar_url = getattr(ctx.author.display_avatar, "url", None) or getattr(ctx.author, "avatar_url", None)
                                embed.set_footer(text=f"Cancelled by {ctx.author}", icon_url=avatar_url)
                                await ch.send(embed=embed)
                    else:
                        txt = await addl.text()
                        await ctx.send(f"Failed to add label: {addl.status} {txt}")

            except Exception as e:
                logger.exception("Error in cancelshift")
                await ctx.send(f"An error occurred: {e}")

    @commands.command(name="setlogs")
    @checks.has_permissions(PermissionLevel.MODERATOR)
    async def setlogs(self, ctx: commands.Context, channel: discord.TextChannel):
        """Sets the channel for Trello scheduling logs."""
        await self.db.find_one_and_update({"_id": "logs_channel"}, {"$set": {"channel_id": str(channel.id)}}, upsert=True)
        self.logs_channel_id = str(channel.id)
        await ctx.send(f"Logs channel set to {channel.mention}")


# Standard setup for discord.py >= 2.0
async def setup(bot: commands.Bot):
    await bot.add_cog(TrelloScheduler(bot))
