import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import json
from datetime import datetime

class SessionScheduler(commands.Cog, name="Session Scheduler"):
    def __init__(self, bot):
        self.bot = bot
        self.log_channels = {}
        self.trello_key = "ac3c79179852faa3868698ec07b41594"
        self.trello_token = "ATTA00e7c47e3440690fe364137898a081e282cd8ca8aa733f791e5fedf780ba6b7314764152"
        self.list_id = "68f444860b7854a2fef52fa4"

    async def get_roblox_user_id(self, username):
        try:
            async with aiohttp.ClientSession() as session:
                url = 'https://users.roblox.com/v1/usernames/users'
                payload = {
                    "usernames": [username],
                    "excludeBannedUsers": True
                }
                async with session.post(url, json=payload) as response:
                    if response.status != 200:
                        return None, None
                    data = await response.json()
                    if data['data']:
                        return data['data'][0]['id'], data['data'][0]['name']
                    else:
                        return None, None
        except Exception as e:
            print(f"Error fetching Roblox user ID: {e}")
            return None, None

    async def create_trello_card(self, name, desc, label_name):
        url = "https://api.trello.com/1/cards"
        query = {
            'key': self.trello_key,
            'token': self.trello_token,
            'idList': self.list_id,
            'name': name,
            'desc': desc
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, params=query) as response:
                if response.status != 200:
                    return None
                card_data = await response.json()
                card_id = card_data['id']

                labels_url = f"https://api.trello.com/1/boards/{await self.get_board_id()}/labels"
                async with session.get(labels_url, params={'key': self.trello_key, 'token': self.trello_token}) as labels_response:
                    if labels_response.status == 200:
                        labels = await labels_response.json()
                        label_id = None
                        for label in labels:
                            if label['name'] == label_name:
                                label_id = label['id']
                                break

                        if label_id:
                            add_label_url = f"https://api.trello.com/1/cards/{card_id}/idLabels"
                            await session.post(add_label_url, params={'key': self.trello_key, 'token': self.trello_token, 'value': label_id})

                return card_id

    async def get_board_id(self):
        url = f"https://api.trello.com/1/lists/{self.list_id}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params={'key': self.trello_key, 'token': self.trello_token}) as response:
                if response.status == 200:
                    data = await response.json()
                    return data['idBoard']
        return None

    async def add_label_to_card(self, card_id, label_name):
        board_id = await self.get_board_id()
        if not board_id:
            return False

        labels_url = f"https://api.trello.com/1/boards/{board_id}/labels"
        async with aiohttp.ClientSession() as session:
            async with session.get(labels_url, params={'key': self.trello_key, 'token': self.trello_token}) as response:
                if response.status == 200:
                    labels = await response.json()
                    label_id = None
                    for label in labels:
                        if label['name'] == label_name:
                            label_id = label['id']
                            break

                    if label_id:
                        add_label_url = f"https://api.trello.com/1/cards/{card_id}/idLabels"
                        async with session.post(add_label_url, params={'key': self.trello_key, 'token': self.trello_token, 'value': label_id}) as add_response:
                            return add_response.status == 200
        return False

    async def get_all_cards(self):
        url = f"https://api.trello.com/1/lists/{self.list_id}/cards"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params={'key': self.trello_key, 'token': self.trello_token}) as response:
                if response.status == 200:
                    return await response.json()
        return []

    @commands.command(name="setlogs", help="Set the log channel for session scheduling. Usage: ?setlogs #channel")
    @commands.has_permissions(administrator=True)
    async def setlogs(self, ctx, channel: discord.TextChannel):
        self.log_channels[ctx.guild.id] = channel.id
        await ctx.send(f"Log channel set to {channel.mention}")

    @commands.command(name="schedulesession", help="Schedule a session. Usage: ?schedulesession [shift/training/largeshift]")
    async def schedulesession(self, ctx, session_type: str):
        session_types = {
            "shift": "Shift",
            "training": "Training Session",
            "largeshift": "LARGE SHIFT"
        }

        if session_type.lower() not in session_types:
            await ctx.send("Invalid session type. Use: shift, training, or largeshift")
            return

        class SessionModal(discord.ui.Modal, title="Schedule Session"):
            host_username = discord.ui.TextInput(label="Host Roblox Username", required=True)
            cohost_username = discord.ui.TextInput(label="Cohost Roblox Username", required=False)
            description = discord.ui.TextInput(label="Description", style=discord.TextStyle.paragraph, required=True)
            date = discord.ui.TextInput(label="Date (MM/DD/YYYY)", required=True)
            time = discord.ui.TextInput(label="Time (24hr format HH:MM)", required=True)

            def __init__(self, cog, session_title):
                super().__init__()
                self.cog = cog
                self.session_title = session_title

            async def on_submit(self, interaction: discord.Interaction):
                host_id, host_name = await self.cog.get_roblox_user_id(str(self.host_username))
                if not host_id:
                    await interaction.response.send_message(f"Could not find Roblox user: {self.host_username}", ephemeral=True)
                    return

                cohost_text = ""
                if str(self.cohost_username):
                    cohost_id, cohost_name = await self.cog.get_roblox_user_id(str(self.cohost_username))
                    if cohost_name:
                        cohost_text = f"\nCohost: {cohost_name}"

                card_desc = f"Host: {host_name}{cohost_text}\nDescription: {self.description}"

                card_id = await self.cog.create_trello_card(self.session_title, card_desc, "Scheduled")

                if card_id:
                    await interaction.response.send_message(f"Session scheduled successfully! Card created on Trello.", ephemeral=True)

                    if interaction.guild.id in self.cog.log_channels:
                        log_channel = interaction.guild.get_channel(self.cog.log_channels[interaction.guild.id])
                        if log_channel:
                            embed = discord.Embed(
                                title="Session Scheduled",
                                description=f"**Type:** {self.session_title}\n**Host:** {host_name}\n**Cohost:** {cohost_name if cohost_text else 'None'}\n**Date:** {self.date}\n**Time:** {self.time}\n**Description:** {self.description}",
                                color=discord.Color.green()
                            )
                            embed.set_footer(text=f"Scheduled by {interaction.user}")
                            await log_channel.send(embed=embed)
                else:
                    await interaction.response.send_message("Failed to create Trello card.", ephemeral=True)

        modal = SessionModal(self, session_types[session_type.lower()])
        await ctx.send("Opening session scheduler...", delete_after=2)
        
        class ModalView(discord.ui.View):
            def __init__(self, modal_instance):
                super().__init__(timeout=180)
                self.modal_instance = modal_instance

            @discord.ui.button(label="Schedule Session", style=discord.ButtonStyle.primary)
            async def schedule_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                await interaction.response.send_modal(self.modal_instance)

        view = ModalView(modal)
        await ctx.send("Click the button to open the session scheduler:", view=view)

    @commands.command(name="cancelsession")
    async def cancelsession(self, ctx, *, session_name: str):
        cards = await self.get_all_cards()
        
        card_found = None
        for card in cards:
            if session_name.lower() in card['name'].lower():
                card_found = card
                break

        if not card_found:
            await ctx.send(f"Could not find a session with name: {session_name}")
            return

        success = await self.add_label_to_card(card_found['id'], "Cancelled")

        if success:
            await ctx.send(f"Session '{card_found['name']}' has been marked as cancelled.")

            if ctx.guild.id in self.log_channels:
                log_channel = ctx.guild.get_channel(self.log_channels[ctx.guild.id])
                if log_channel:
                    embed = discord.Embed(
                        title="Session Cancelled",
                        description=f"**Session:** {card_found['name']}\n**Cancelled by:** {ctx.author}",
                        color=discord.Color.red()
                    )
                    await log_channel.send(embed=embed)
        else:
            await ctx.send("Failed to cancel the session.")

async def setup(bot):
    await bot.add_cog(SessionScheduler(bot))
