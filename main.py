import asyncio
import os
from typing import Dict, Optional
from aiohttp import web
import discord
from discord import app_commands
from discord.ext import commands

# ---------------------------------------------------------
# RENDER KEEP-ALIVE SERVER CONFIGURATION
# ---------------------------------------------------------


async def handle_ping(request: web.Request) -> web.Response:
  """Health check endpoint for Render Web Services."""
  return web.Response(text="Bot is running smoothly!", status=200)


async def start_background_webserver():
  """Starts a lightweight web server bound to the Render PORT environment variable."""
  app = web.Application()
  app.router.add_get("/", handle_ping)
  app.router.add_get("/health", handle_ping)

  runner = web.AppRunner(app)
  await runner.setup()

  # Render assigns an environmental PORT (default fallback to 8080)
  port = int(os.environ.get("PORT", 8080))
  site = web.TCPSite(runner, "0.0.0.0", port)
  await site.start()
  print(f"[HTTP] Keep-alive server listening on 0.0.0.0:{port}")


# ---------------------------------------------------------
# GAME ENGINE & STATE MANAGEMENT
# ---------------------------------------------------------


class GuessSession:
  """Encapsulates the state of an active guessing game session."""

  def __init__(
      self,
      channel_id: int,
      host: discord.Member | discord.User,
      target: int,
      min_val: int,
      max_val: int,
      prize: str,
  ):
    self.channel_id: int = channel_id
    self.host: discord.Member | discord.User = host
    self.target: int = target
    self.min_val: int = min_val
    self.max_val: int = max_val
    self.prize: str = prize
    self.total_attempts: int = 0
    self.is_active: bool = True

  def check_guess(self, guess: int) -> bool:
    self.total_attempts += 1
    return guess == self.target


# Per-channel active game storage
active_sessions: Dict[int, GuessSession] = {}


# ---------------------------------------------------------
# DISCORD UI (BUTTONS, MODALS & EMBEDS)
# ---------------------------------------------------------


class SessionControlView(discord.ui.View):
  """Host GUI Control Dashboard with interactive action buttons."""

  def __init__(self, session: GuessSession):
    super().__init__(timeout=None)
    self.session = session

  @discord.ui.button(
      label="Game Stats",
      style=discord.ButtonStyle.secondary,
      emoji="📊",
      custom_id="btn_stats",
  )
  async def stats_button(
      self, interaction: discord.Interaction, button: discord.ui.Button
  ):
    if not self.session.is_active:
      await interaction.response.send_message(
          "This giveaway has already completed.", ephemeral=True
      )
      return

    embed = discord.Embed(
        title="📊 Giveaway Status",
        description="Current live statistics for this channel:",
        color=discord.Color.blue(),
    )
    embed.add_field(
        name="Range",
        value=f"`{self.session.min_val}` — `{self.session.max_val}`",
        inline=True,
    )
    embed.add_field(
        name="Total Guesses",
        value=f"`{self.session.total_attempts}`",
        inline=True,
    )
    embed.add_field(name="Prize", value=self.session.prize, inline=False)
    embed.set_footer(text=f"Hosted by {self.session.host.display_name}")

    await interaction.response.send_message(embed=embed, ephemeral=True)

  @discord.ui.button(
      label="Cancel Event",
      style=discord.ButtonStyle.danger,
      emoji="🛑",
      custom_id="btn_cancel",
  )
  async def cancel_button(
      self, interaction: discord.Interaction, button: discord.ui.Button
  ):
    # Only allow the host or Server Admins to terminate
    if (
        interaction.user.id != self.session.host.id
        and not interaction.user.guild_permissions.administrator
    ):
      await interaction.response.send_message(
          "⛔ Only the giveaway host or an administrator can cancel this event.",
          ephemeral=True,
      )
      return

    self.session.is_active = False
    active_sessions.pop(self.session.channel_id, None)

    # Disable buttons
    for child in self.children:
      child.disabled = True
    await interaction.message.edit(view=self)

    cancel_embed = discord.Embed(
        title="🛑 Giveaway Cancelled",
        description=(
            f"The giveaway was cancelled by {interaction.user.mention}.\n"
            f"The secret number was: **{self.session.target}**."
        ),
        color=discord.Color.red(),
    )
    await interaction.channel.send(embed=cancel_embed)
    await interaction.response.send_message(
        "Event terminated successfully.", ephemeral=True
    )


class SetupModal(discord.ui.Modal, title="Create Number Guess Giveaway"):
  """Discord Native Modal GUI pop-up to configure giveaway parameters."""

  min_input = discord.ui.TextInput(
      label="Minimum Number",
      placeholder="e.g. 1",
      required=True,
      max_length=8,
  )

  max_input = discord.ui.TextInput(
      label="Maximum Number",
      placeholder="e.g. 500",
      required=True,
      max_length=8,
  )

  secret_input = discord.ui.TextInput(
      label="Secret Number (Winning Target)",
      placeholder="The exact number users must guess to win",
      required=True,
      max_length=8,
  )

  prize_input = discord.ui.TextInput(
      label="Prize / Reward Description",
      placeholder="e.g. Nitro Basic, 1,000 Credits, Steam Key",
      required=False,
      default="Host will provide custom reward",
      max_length=150,
  )

  async def on_submit(self, interaction: discord.Interaction):
    # Validate numerical types
    try:
      min_val = int(self.min_input.value.strip())
      max_val = int(self.max_input.value.strip())
      secret_num = int(self.secret_input.value.strip())
    except ValueError:
      await interaction.response.send_message(
          "❌ Invalid input: Min, Max, and Secret Number must be whole numbers.",
          ephemeral=True,
      )
      return

    # Boundary check
    if min_val >= max_val:
      await interaction.response.send_message(
          f"❌ Minimum ({min_val}) must be strictly less than Maximum ({max_val}).",
          ephemeral=True,
      )
      return

    if not (min_val <= secret_num <= max_val):
      await interaction.response.send_message(
          f"❌ Secret number (`{secret_num}`) must lie within `{min_val}` and `{max_val}`.",
          ephemeral=True,
      )
      return

    # Instantiate the active session
    session = GuessSession(
        channel_id=interaction.channel_id,
        host=interaction.user,
        target=secret_num,
        min_val=min_val,
        max_val=max_val,
        prize=self.prize_input.value.strip(),
    )
    active_sessions[interaction.channel_id] = session

    # Ephemeral feedback to Host
    await interaction.response.send_message(
        f"✅ **Giveaway live!** Secret number `{secret_num}` has been locked in. Chat is open for spam.",
        ephemeral=True,
    )

    # Public Game Announcement Embed
    public_embed = discord.Embed(
        title="🎯 NUMBER GUESSING GIVEAWAY",
        description=(
            "A target number has been selected! Start typing numbers in the chat to win.\n\n"
            "⚡ **Spam Allowed:** Guess as many times and as fast as you want!"
        ),
        color=discord.Color.gold(),
    )
    public_embed.add_field(
        name="Range", value=f"`{min_val}` — `{max_val}`", inline=True
    )
    public_embed.add_field(name="Prize", value=session.prize, inline=True)
    public_embed.add_field(
        name="Host", value=interaction.user.mention, inline=False
    )
    public_embed.set_footer(
        text="The first member to hit the target number wins immediately."
    )

    view = SessionControlView(session)
    await interaction.channel.send(embed=public_embed, view=view)


# ---------------------------------------------------------
# BOT CORE CLIENT
# ---------------------------------------------------------


class GiveawayBot(commands.Bot):

  def __init__(self):
    intents = discord.Intents.default()
    intents.message_content = True
    super().__init__(command_prefix="!", intents=intents)

  async def setup_hook(self):
    # Initialize Render web server background task
    self.loop.create_task(start_background_webserver())
    # Sync modern Discord Application Commands
    await self.tree.sync()
    print("[INIT] Slash Command Application Tree synced globally.")

  async def on_ready(self):
    print(f"[ONLINE] System initialized as {self.user.name} ({self.user.id})")
    await self.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.competing, name="Number Guessing Events"
        )
    )


bot = GiveawayBot()

# ---------------------------------------------------------
# APPLICATION COMMANDS (SLASH GUI ENTRY)
# ---------------------------------------------------------


@bot.tree.command(
    name="giveaway",
    description="Launch an interactive number guessing giveaway.",
)
@app_commands.checks.has_permissions(manage_messages=True)
async def giveaway(interaction: discord.Interaction):
  """Opens the graphical input modal to setup the event."""
  if interaction.channel_id in active_sessions:
    await interaction.response.send_message(
        "⚠️ An active giveaway is already underway in this channel! Finish or cancel it first.",
        ephemeral=True,
    )
    return

  # Render Discord Modal GUI
  await interaction.response.send_modal(SetupModal())


@giveaway.error
async def giveaway_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
):
  if isinstance(error, app_commands.MissingPermissions):
    await interaction.response.send_message(
        "⛔ You do not have permission to launch giveaways. (Required: Manage Messages)",
        ephemeral=True,
    )


# ---------------------------------------------------------
# GUESS PROCESSING LISTENER (HIGH-THROUGHPUT SPAM READY)
# ---------------------------------------------------------


@bot.event
async def on_message(message: discord.Message):
  # Ignore bots and non-session channels immediately
  if message.author.bot or message.channel.id not in active_sessions:
    return

  session = active_sessions.get(message.channel.id)
  if not session or not session.is_active:
    return

  raw_content = message.content.strip()

  # Quick numeric extraction
  if raw_content.isdigit():
    try:
      guess = int(raw_content)
    except ValueError:
      return

    # Check boundaries quietly to keep chat throughput clean
    if not (session.min_val <= guess <= session.max_val):
      return

    # Winning evaluation
    if session.check_guess(guess):
      # Lock session to prevent race conditions from concurrent spam
      session.is_active = False
      active_sessions.pop(message.channel.id, None)

      # Winner Embed
      win_embed = discord.Embed(
          title="🎉 WE HAVE A WINNER! 🎉",
          description=(
              f"Congratulations to {message.author.mention} for hitting the exact number!\n\n"
              f"• **Winning Number:** `{session.target}`\n"
              f"• **Total Channel Guesses:** `{session.total_attempts}`\n"
              f"• **Prize:** {session.prize}"
          ),
          color=discord.Color.green(),
      )
      win_embed.set_thumbnail(
          url=message.author.display_avatar.url
          if message.author.display_avatar
          else None
      )
      win_embed.set_footer(text="Giveaway completed successfully.")

      # Broadcast winner and alert the host directly
      await message.channel.send(
          content=f"🔔 {session.host.mention} — **A winner has been found!**",
          embed=win_embed,
      )
      return

  await bot.process_commands(message)


# ---------------------------------------------------------
# BOT STARTUP
# ---------------------------------------------------------

if __name__ == "__main__":
  token = os.environ.get("DISCORD_TOKEN")
  if not token:
    raise ValueError(
        "Missing DISCORD_TOKEN environment variable. Set it in your Render Environment settings."
    )

  bot.run(token)
