import discord
from discord.ext import commands
import json
import os

# === CONFIG ===
ALLOWED_ROLES = [1427281425635217428]
AFFILIATE_LIST_CHANNEL = 1418267668062142654
PARTNER_LOGS_CHANNEL = 1421997721463685150
DATA_FILE = "affiliates.json"

# === LOAD/SAVE DATA ===
def load_affiliates():
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_affiliates(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

# === MAIN COG ===
class Affiliate(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.affiliates = load_affiliates()

    def has_allowed_role(self, member):
        return any(role.id in ALLOWED_ROLES for role in member.roles)

    async def update_affiliate_list(self, guild):
        """Auto-update the affiliate list channel"""
        channel = guild.get_channel(AFFILIATE_LIST_CHANNEL)
        if not channel:
            return

        if not self.affiliates:
            text = "## Jimmy John's | Affiliates List\n*No affiliates registered yet.*"
        else:
            lines = [f"## Affiliates List"]
            for name, data in self.affiliates.items():
                reps = ", ".join(data["representatives"])
                lines.append(f"**{name} | {reps}**")
            lines.append("\n*This list will auto-update when we have new affiliates*")
            text = "\n".join(lines)

        try:
            # Get the last message (assuming the bot owns it)
            messages = [msg async for msg in channel.history(limit=1)]
            if messages and messages[0].author == self.bot.user:
                await messages[0].edit(content=text)
            else:
                await channel.send(text)
        except Exception as e:
            print(f"[Affiliate] Failed to update list: {e}")

    @commands.command(name="register")
    async def register_affiliate(self, ctx, affiliate_name: str, reps: str, *, person_in_charge: str):
        """Register a new affiliate."""
        if not self.has_allowed_role(ctx.author):
            return await ctx.send("You don't have permission to use this command.")

        if affiliate_name in self.affiliates:
            return await ctx.send("This affiliate is already registered.")

        reps_list = [r.strip() for r in reps.split(",")]
        self.affiliates[affiliate_name] = {
            "representatives": reps_list,
            "person_in_charge": person_in_charge,
        }
        save_affiliates(self.affiliates)

        # Log it
        log_channel = ctx.guild.get_channel(PARTNER_LOGS_CHANNEL)
        if log_channel:
            await log_channel.send(
                f"**{affiliate_name}**\n"
                f"Name of Affiliate: {affiliate_name}\n"
                f"Affiliate Representatives: {', '.join(reps_list)}\n"
                f"Person In Charge: {person_in_charge}"
            )

        await self.update_affiliate_list(ctx.guild)
        await ctx.send(f"**{affiliate_name}** registered successfully.")

    @commands.command(name="unregister")
    async def unregister_affiliate(self, ctx, *, affiliate_name: str):
        """Remove an affiliate."""
        if not self.has_allowed_role(ctx.author):
            return await ctx.send("You don't have permission to use this command.")

        if affiliate_name not in self.affiliates:
            return await ctx.send("Affiliate not found.")

        del self.affiliates[affiliate_name]
        save_affiliates(self.affiliates)

        log_channel = ctx.guild.get_channel(PARTNER_LOGS_CHANNEL)
        if log_channel:
            await log_channel.send(f"Affiliate **{affiliate_name}** has been unregistered.")

        await self.update_affiliate_list(ctx.guild)
        await ctx.send(f"**{affiliate_name}** unregistered successfully.")

async def setup(bot):
    await bot.add_cog(Affiliate(bot))
