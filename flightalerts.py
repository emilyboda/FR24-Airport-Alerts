import discord
import asyncio
from discord.ext import commands, tasks
from discord import app_commands
from datetime import datetime, time
import pytz
from fr24sdk.client import Client
import logging

# =========================
# Settings for the program
# =========================
API_TOKEN = '<fr24 api token>' 
DISCORD_TOKEN = '<discord token>'
CHANNEL_ID = '<discord channel id>'

# ================
# Discord Intents
# ================
intents = discord.Intents.default()
intents.message_content = True

# ==========================
# Initialize client and bot
# ==========================
client = Client(api_token=API_TOKEN)
bot = commands.Bot(command_prefix="!", intents=intents)


# ===============
# Global settings
# ===============
airports = []  # e.g. ["BGI", "MNI"]
start_time = time(9, 0)   # 9:00 AM by default
end_time = time(22, 0)    # 10:00 PM by default
debug_mode = False
script_running = True
override_mode = False
aircraft_count_today = 0
aircraft_count_month = 0
last_checked_date = datetime.now(
    pytz.timezone("America/New_York")
).date()
MAX_AIRCRAFT_PER_DAY = 300  # This is based on each flight returned costing 6 credits, 
                            # and each month getting 60,000 credits with the $9 plan.

# ============
# Logging setup
# ============
logging.basicConfig(level=logging.INFO)


# ==================
# Helper: send to DC
# ==================
async def send_message(message: str):
    """Send a message to the fixed Discord channel."""
    channel = bot.get_channel(int(CHANNEL_ID))
    if channel is None:
        logging.error("Could not find channel with ID %s", CHANNEL_ID)
        return
    await channel.send(message)


# =======================
# FR24 API wrapper
# =======================
def get_aircraft_data():
    """Query FR24 for inbound flights to the configured airports."""
    global airports

    # Default airport if none set
    if not airports:
        airports.append("ANU")

    try:
        airports_query = [f"inbound:{airport}" for airport in airports]
        print("🔍 Querying FR24 with airports:", airports_query)

        flight_positions_response = client.live.flight_positions.get_light(
            airports=airports_query
        )

        data = getattr(flight_positions_response, "data", flight_positions_response)

        if data is None:
            data = []

        print(f"🛩️ Received {len(data)} flights")

        return data

    except Exception as e:
        logging.error(f"Error querying API: {e}")
        return []


# ==========================
# Daily counter reset helper
# ==========================
def reset_daily_counts():
    global aircraft_count_today, last_checked_date
    current_date = datetime.now(
        pytz.timezone("America/New_York")
    ).date()
    if current_date != last_checked_date:
        aircraft_count_today = 0
        last_checked_date = current_date


# ==========================
# Core processing logic
# ==========================
async def process_aircraft_data():
    """Fetch and process aircraft data, send any matching flights to Discord."""
    global aircraft_count_today, aircraft_count_month

    flight_positions = get_aircraft_data()
    if not flight_positions:
        if debug_mode:
            await send_message("No aircraft returned")
        return

    messages = []

    for flight in flight_positions:
        callsign = getattr(flight, "callsign", None)
        lat = getattr(flight, "lat", None)
        lon = getattr(flight, "lon", None)

        if not callsign or lat is None or lon is None:
            continue

        lat_fmt = f"{lat:.2f}"
        lon_fmt = f"{lon:.2f}"

        destination = ", ".join(airports) if airports else "unknown"

        print(f"📡 {callsign} -> {destination} @ {lat_fmt}, {lon_fmt}")

        flight_link = f"https://flightradar24.com/{callsign}"

        message = (
            f"✈️ [{callsign}]({flight_link}) is heading to {destination} "
            f"from {lat_fmt}, {lon_fmt}"
        )

        messages.append(message)

    if messages:
        aircraft_count_today += len(messages)
        aircraft_count_month += len(messages)

        if aircraft_count_today <= MAX_AIRCRAFT_PER_DAY or override_mode:
            await send_message("\n".join(messages))

        if aircraft_count_today >= MAX_AIRCRAFT_PER_DAY:
            await send_message(
                f"Max number of aircraft returned per day "
                f"({MAX_AIRCRAFT_PER_DAY}) is reached."
            )


# =========================================
# Wrapper that enforces time window & state
# =========================================
async def check_and_run_query():
    """Check time window & script status, then run processing if allowed."""
    current_time = datetime.now(
        pytz.timezone("America/New_York")
    ).time()
    print("⏰ check_and_run_query at (local):", current_time)

    if not script_running:
        print("⛔ Script is OFF; not querying FR24")
        return

    if not (start_time <= current_time <= end_time):
        print("⛔ Outside active time window; not querying FR24")
        return

    if aircraft_count_today >= MAX_AIRCRAFT_PER_DAY and not override_mode:
        print("⛔ Daily aircraft limit reached and no override; not querying FR24")
        return

    print("✅ Conditions met; running process_aircraft_data()")
    await process_aircraft_data()


# ===========================
# Background task (5 min loop)
# ===========================
@tasks.loop(minutes=5)
async def check_flights():
    reset_daily_counts()
    await check_and_run_query()


# ==================
# Slash commands
# ==================

@bot.tree.command(
    name="airports",
    description="Set the list of destination airports to monitor (comma-separated)."
)
async def airports_cmd(interaction: discord.Interaction, codes: str):
    """
    Example: /airports BGI,MNI,ESSA
    """
    global airports
    # Split on comma, strip spaces, ignore empties
    airports = [c.strip().upper() for c in codes.replace(" ", "").split(",") if c.strip()]
    if not airports:
        msg = "Airports list cleared."
    else:
        msg = f"Airports set to: {', '.join(airports)}"

    await interaction.response.send_message(msg)

    # Immediately query after change
    await check_and_run_query()


@bot.tree.command(
    name="start",
    description="Set the local start time for queries (e.g. 9:00am)."
)
async def start_cmd(interaction: discord.Interaction, time_str: str):
    """
    Example: /start 9:00am
    """
    global start_time
    try:
        start_time = datetime.strptime(time_str, "%I:%M%p").time()
        msg = f"Start time set to: {start_time.strftime('%I:%M %p')}"
    except ValueError:
        msg = "Invalid time format. Use like: 9:00am or 10:30pm"
        await interaction.response.send_message(msg, ephemeral=True)
        return

    await interaction.response.send_message(msg)
    await check_and_run_query()


@bot.tree.command(
    name="end",
    description="Set the local end time for queries (e.g. 10:00pm)."
)
async def end_cmd(interaction: discord.Interaction, time_str: str):
    """
    Example: /end 10:00pm
    """
    global end_time
    try:
        end_time = datetime.strptime(time_str, "%I:%M%p").time()
        msg = f"End time set to: {end_time.strftime('%I:%M %p')}"
    except ValueError:
        msg = "Invalid time format. Use like: 9:00am or 10:30pm"
        await interaction.response.send_message(msg, ephemeral=True)
        return

    await interaction.response.send_message(msg)
    await check_and_run_query()


@bot.tree.command(
    name="debug",
    description="Turn debugging mode On or Off."
)
async def debug_cmd(interaction: discord.Interaction, status: str):
    """
    Example: /debug On  or  /debug Off
    """
    global debug_mode
    if status.lower() == "on":
        debug_mode = True
        msg = "Debugging mode turned ON."
    elif status.lower() == "off":
        debug_mode = False
        msg = "Debugging mode turned OFF."
    else:
        msg = "Invalid option. Use 'On' or 'Off'."
        await interaction.response.send_message(msg, ephemeral=True)
        return

    await interaction.response.send_message(msg)
    await check_and_run_query()


@bot.tree.command(
    name="script",
    description="Turn the script On or Off."
)
async def script_cmd(interaction: discord.Interaction, status: str):
    """
    Example: /script On  or  /script Off
    """
    global script_running
    if status.lower() == "on":
        script_running = True
        msg = "Script turned ON."
        await interaction.response.send_message(msg)
        # Only query if we just turned it ON
        await check_and_run_query()
    elif status.lower() == "off":
        script_running = False
        msg = "Script turned OFF."
        await interaction.response.send_message(msg)
    else:
        msg = "Invalid option. Use 'On' or 'Off'."
        await interaction.response.send_message(msg, ephemeral=True)


@bot.tree.command(
    name="override",
    description="Turn override mode On or Off (ignores daily limit)."
)
async def override_cmd(interaction: discord.Interaction, status: str):
    """
    Example: /override On  or  /override Off
    """
    global override_mode
    if status.lower() == "on":
        override_mode = True
        msg = "Override mode turned ON."
    elif status.lower() == "off":
        override_mode = False
        msg = "Override mode turned OFF."
    else:
        msg = "Invalid option. Use 'On' or 'Off'."
        await interaction.response.send_message(msg, ephemeral=True)
        return

    await interaction.response.send_message(msg)
    await check_and_run_query()


@bot.tree.command(
    name="info",
    description="Show current FR24 alert settings and stats."
)
async def info_cmd(interaction: discord.Interaction):
    global airports, start_time, end_time
    info = (
        f"Airports: {', '.join(airports) if airports else 'None'}\n"
        f"Time period: {start_time.strftime('%I:%M %p')} - "
        f"{end_time.strftime('%I:%M %p')}\n"
        f"Override mode: {'On' if override_mode else 'Off'}\n"
        f"Debug mode: {'On' if debug_mode else 'Off'}\n"
        f"Script status: {'On' if script_running else 'Off'}\n"
        f"Aircraft returned today: {aircraft_count_today}\n"
        f"Aircraft returned this month: {aircraft_count_month}\n"
    )
    await interaction.response.send_message(info)


# ==========
# on_ready
# ==========
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} slash command(s).")
    except Exception as e:
        print(f"Error syncing commands: {e}")

    reset_daily_counts()
    await check_and_run_query()
    if not check_flights.is_running():
        check_flights.start()


# ==========
# Run bot
# ==========
bot.run(DISCORD_TOKEN)
