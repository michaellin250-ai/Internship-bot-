"""
Discord Internship Bot
======================
Monitors GitHub internship repos and posts new CS / PM listings to a Discord channel.

Sources:
  - SimplifyJobs/Summer2026-Internships  (primary)
  - SimplifyJobs/New-Grad-Positions      (optional, disabled by default)

Setup:
  1. pip install -r requirements.txt
  2. Copy .env.example → .env and fill in your values
  3. python bot.py
"""

import asyncio
import json
import os
import re
import hashlib
from datetime import datetime, timezone

import discord
from discord.ext import tasks
import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

load_dotenv()

TOKEN               = os.getenv("DISCORD_TOKEN")
CHANNEL_ID          = int(os.getenv("CHANNEL_ID", "0"))
CHECK_INTERVAL_MINS = int(os.getenv("CHECK_INTERVAL_MINUTES", "20"))
TRACKER_FILE        = "posted_internships.json"

# GitHub raw README URLs to monitor
SOURCES = [
    {
        "label": "Summer 2026 Internships",
        "url":   "https://raw.githubusercontent.com/SimplifyJobs/Summer2026-Internships/dev/README.md",
    },
    # Uncomment to also monitor new-grad positions:
    # {
    #     "label": "New Grad 2026",
    #     "url":   "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/README.md",
    # },
]

# ---------------------------------------------------------------------------
# Role-filter keywords
# ---------------------------------------------------------------------------

CS_KEYWORDS = [
    "software", "engineer", "developer", "swe", "backend", "front-end",
    "frontend", "full stack", "fullstack", "full-stack", "data", "machine learning",
    "deep learning", "ml", "ai ", "artificial intelligence", "computer science",
    " cs ", "devops", "cloud", "security", "cybersecurity", "embedded", "systems",
    "mobile", "ios", "android", "web", "api", "infrastructure", "platform",
    "site reliability", "sre", "qa ", "quality assurance", "test", "automation",
    "network", "database", "firmware", "robotics", "computer vision",
]

PM_KEYWORDS = [
    "product manager", "product management", "product intern",
    "pm intern", "associate pm", "apm", "technical product",
    "associate product manager",
]

ALL_KEYWORDS = CS_KEYWORDS + PM_KEYWORDS


def is_relevant(role: str) -> bool:
    """Return True if the role matches CS or PM keywords."""
    role_lower = f" {role.lower()} "
    return any(kw in role_lower for kw in ALL_KEYWORDS)


def role_category(role: str):
    """Return (emoji, label) tuple for the role category."""
    role_lower = role.lower()
    if any(kw in role_lower for kw in PM_KEYWORDS):
        return "📋", "Product Management"
    return "💻", "Software / CS"


# ---------------------------------------------------------------------------
# Tracker (persist which listings have already been posted)
# ---------------------------------------------------------------------------

def load_tracker() -> set:
    if os.path.exists(TRACKER_FILE):
        with open(TRACKER_FILE, "r") as f:
            data = json.load(f)
            return set(data.get("posted", []))
    return set()


def save_tracker(posted_ids: set) -> None:
    with open(TRACKER_FILE, "w") as f:
        json.dump({"posted": list(posted_ids)}, f, indent=2)


def make_id(company: str, role: str, apply_url: str) -> str:
    """Stable unique ID for a listing."""
    raw = f"{company.lower()}|{role.lower()}|{apply_url}"
    return hashlib.md5(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Markdown table parser
# ---------------------------------------------------------------------------

# Strips markdown links like [text](url) → text
_LINK_TEXT_RE  = re.compile(r"\[([^\]]*)\]\([^)]*\)")
# Extracts the first URL from a markdown cell
_LINK_URL_RE   = re.compile(r"\[(?:[^\]]*)\]\(([^)]+)\)")
# HTML tags (SimplifyJobs uses <br> for multi-location)
_HTML_TAG_RE   = re.compile(r"<[^>]+>")
# Separator rows: | :---: | --- |  (just needs to start with | and contain only separator chars)
_SEPARATOR_RE  = re.compile(r"^\|[\s\-:|]+")


def _clean(text: str) -> str:
    text = _HTML_TAG_RE.sub(" ", text)
    text = _LINK_TEXT_RE.sub(r"\1", text)
    return text.strip()


def parse_readme(content: str) -> list[dict]:
    """
    Parse a SimplifyJobs-style README markdown table and return a list of
    internship dicts for CS/PM roles only.
    """
    listings = []
    header_passed = False
    in_table = False

    for raw_line in content.splitlines():
        line = raw_line.strip()

        if not line.startswith("|"):
            if in_table:
                # We just left a table block — reset state
                in_table = False
                header_passed = False
            continue

        in_table = True

        # Skip separator rows
        if _SEPARATOR_RE.match(line):
            header_passed = True
            continue

        if not header_passed:
            # This is the header row — skip it
            continue

        # Split into cells and strip surrounding whitespace
        cells = [c.strip() for c in line.split("|")]
        cells = [c for c in cells if c != ""]   # remove empties from leading/trailing |

        if len(cells) < 4:
            continue

        company_cell  = cells[0]
        role_cell     = cells[1]
        location_cell = cells[2]
        apply_cell    = cells[3]
        date_cell     = cells[4] if len(cells) > 4 else ""

        # Role continuations are marked with ↳  — skip
        if "↳" in role_cell and _clean(role_cell).strip() == "↳":
            continue

        role = _clean(role_cell)
        if not role or not is_relevant(role):
            continue

        # Company name + optional URL
        company_name = _clean(company_cell)
        company_url_m = _LINK_URL_RE.search(company_cell)
        company_url = company_url_m.group(1) if company_url_m else None

        # Location — may contain <br> separators
        location = location_cell.replace("<br>", ", ").replace("<br/>", ", ")
        location = _clean(location) or "Not specified"

        # Apply links — grab the first one
        apply_urls = _LINK_URL_RE.findall(apply_cell)
        if not apply_urls:
            continue
        apply_url = apply_urls[0]

        # Date
        date_posted = _clean(date_cell) or "—"

        listings.append({
            "id":           make_id(company_name, role, apply_url),
            "company":      company_name,
            "company_url":  company_url,
            "role":         role,
            "location":     location,
            "apply_url":    apply_url,
            "date_posted":  date_posted,
        })

    return listings


# ---------------------------------------------------------------------------
# Discord embed builder
# ---------------------------------------------------------------------------

def build_embed(listing: dict, source_label: str) -> discord.Embed:
    emoji, category = role_category(listing["role"])

    color = discord.Color.blurple() if category == "Software / CS" else discord.Color.purple()

    title = f"{listing['company']}  —  {listing['role']}"
    embed = discord.Embed(
        title=title[:256],          # Discord limit
        url=listing["apply_url"],
        color=color,
        timestamp=datetime.now(timezone.utc),
    )

    embed.add_field(name="📍 Location",    value=listing["location"][:1024],  inline=True)
    embed.add_field(name=f"{emoji} Type", value=category,                     inline=True)
    embed.add_field(name="📅 Posted",      value=listing["date_posted"],       inline=True)
    embed.add_field(
        name="🔗 Apply",
        value=f"[Click here to apply]({listing['apply_url']})",
        inline=False,
    )

    if listing.get("company_url"):
        embed.set_author(name=listing["company"], url=listing["company_url"])
    else:
        embed.set_author(name=listing["company"])

    embed.set_footer(text=f"Source: {source_label}")
    return embed


# ---------------------------------------------------------------------------
# Discord bot
# ---------------------------------------------------------------------------

intents = discord.Intents.default()
client  = discord.Client(intents=intents)


@client.event
async def on_ready():
    print(f"✅  Logged in as {client.user}")
    print(f"📡  Checking every {CHECK_INTERVAL_MINS} minute(s) — channel ID {CHANNEL_ID}")
    check_and_post.start()


@tasks.loop(minutes=CHECK_INTERVAL_MINS)
async def check_and_post():
    channel = client.get_channel(CHANNEL_ID)
    if channel is None:
        print(f"❌  Channel {CHANNEL_ID} not found. Check your CHANNEL_ID in .env")
        return

    posted_ids = load_tracker()
    new_total  = 0

    for source in SOURCES:
        print(f"🔍  Fetching: {source['label']} ...")
        try:
            resp = requests.get(source["url"], timeout=20)
            resp.raise_for_status()
        except requests.RequestException as exc:
            print(f"⚠️   Could not fetch {source['label']}: {exc}")
            continue

        listings = parse_readme(resp.text)
        new_listings = [l for l in listings if l["id"] not in posted_ids]
        print(f"    {len(listings)} relevant listings found, {len(new_listings)} new.")

        for listing in new_listings:
            try:
                embed = build_embed(listing, source["label"])
                await channel.send(embed=embed)
                posted_ids.add(listing["id"])
                new_total += 1
                await asyncio.sleep(1.2)   # stay well within Discord rate limits
            except discord.HTTPException as exc:
                print(f"⚠️   Discord error posting {listing['company']}: {exc}")

    save_tracker(posted_ids)

    if new_total:
        print(f"✅  Posted {new_total} new internship(s) at {datetime.now().strftime('%H:%M:%S')}")
    else:
        print(f"ℹ️   No new internships at {datetime.now().strftime('%H:%M:%S')}")


@check_and_post.before_loop
async def before_loop():
    await client.wait_until_ready()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("❌  DISCORD_TOKEN is not set. Copy .env.example → .env and fill it in.")
    if CHANNEL_ID == 0:
        raise SystemExit("❌  CHANNEL_ID is not set. Copy .env.example → .env and fill it in.")

    client.run(TOKEN)
