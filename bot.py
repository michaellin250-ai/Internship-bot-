"""
Discord Internship Bot
======================
Monitors SimplifyJobs/Summer2026-Internships and posts new CS / PM / AI listings
to location-specific Discord channels.

Channels:
  CHANNEL_REMOTE      → remote internships
  CHANNEL_CALIFORNIA  → CA internships (SF, LA, San Jose, etc.)
  CHANNEL_WASHINGTON  → WA internships (Seattle, Redmond, etc.)
  CHANNEL_NEW_YORK    → NY internships (NYC, etc.)
  CHANNEL_GENERAL     → everything else

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
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

load_dotenv()

TOKEN               = os.getenv("DISCORD_TOKEN")
CHECK_INTERVAL_MINS = int(os.getenv("CHECK_INTERVAL_MINUTES", "20"))
TRACKER_FILE        = "posted_internships.json"

# Location channel IDs — set these in Railway Variables (or .env)
CHANNEL_REMOTE     = int(os.getenv("CHANNEL_REMOTE",     "0"))
CHANNEL_CALIFORNIA = int(os.getenv("CHANNEL_CALIFORNIA", "0"))
CHANNEL_WASHINGTON = int(os.getenv("CHANNEL_WASHINGTON", "0"))
CHANNEL_NEW_YORK   = int(os.getenv("CHANNEL_NEW_YORK",   "0"))
CHANNEL_GENERAL    = int(os.getenv("CHANNEL_GENERAL",    "0"))

SOURCES = [
    {
        "label": "Summer 2026 Internships",
        "url":   "https://raw.githubusercontent.com/SimplifyJobs/Summer2026-Internships/dev/README.md",
    },
]

# Sections to scrape from the README
TARGET_SECTIONS = {
    "Software Engineering": ("💻", "Software / CS",      discord.Color.blurple()),
    "Product Management":   ("📋", "Product Management", discord.Color.purple()),
    "Data Science":         ("🤖", "Data Science / AI",  discord.Color.green()),
}

# ---------------------------------------------------------------------------
# Location routing
# ---------------------------------------------------------------------------

# Keywords for each location channel (checked against lowercased location string)
LOCATION_KEYWORDS = {
    "remote":     ["remote", "anywhere", "work from home", "wfh"],
    "california": ["california", " ca,", " ca ", ", ca", "san francisco", "sf,", " sf ",
                   "los angeles", " la,", " la ", "san jose", "san diego", "santa clara",
                   "palo alto", "menlo park", "mountain view", "sunnyvale", "irvine",
                   "berkeley", "oakland", "sacramento", "san mateo", "redwood city"],
    "washington": ["washington", " wa,", " wa ", ", wa", "seattle", "redmond", "bellevue",
                   "kirkland", "tacoma", "spokane", "bothell"],
    "new_york":   ["new york", " ny,", " ny ", ", ny", "nyc", "brooklyn", "manhattan",
                   "queens", "bronx", "albany", "buffalo"],
}


def get_location_channel(location: str) -> str:
    """Return the channel key ('remote', 'california', etc.) for a location string."""
    loc = location.lower()

    # Check remote first — a listing can say "Remote, CA" and we still want remote
    if any(kw in loc for kw in LOCATION_KEYWORDS["remote"]):
        return "remote"
    if any(kw in loc for kw in LOCATION_KEYWORDS["california"]):
        return "california"
    if any(kw in loc for kw in LOCATION_KEYWORDS["washington"]):
        return "washington"
    if any(kw in loc for kw in LOCATION_KEYWORDS["new_york"]):
        return "new_york"
    return "general"


CHANNEL_MAP = {
    "remote":     lambda: CHANNEL_REMOTE,
    "california": lambda: CHANNEL_CALIFORNIA,
    "washington": lambda: CHANNEL_WASHINGTON,
    "new_york":   lambda: CHANNEL_NEW_YORK,
    "general":    lambda: CHANNEL_GENERAL,
}

CHANNEL_LABELS = {
    "remote":     "🌐 Remote",
    "california": "🌴 California",
    "washington": "🌲 Washington",
    "new_york":   "🗽 New York",
    "general":    "🌍 Other",
}

# ---------------------------------------------------------------------------
# Tracker
# ---------------------------------------------------------------------------

def load_tracker() -> set:
    if os.path.exists(TRACKER_FILE):
        with open(TRACKER_FILE, "r") as f:
            return set(json.load(f).get("posted", []))
    return set()


def save_tracker(posted_ids: set) -> None:
    with open(TRACKER_FILE, "w") as f:
        json.dump({"posted": list(posted_ids)}, f, indent=2)


def make_id(company: str, role: str, apply_url: str) -> str:
    raw = f"{company.lower()}|{role.lower()}|{apply_url}"
    return hashlib.md5(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# HTML table parser
# ---------------------------------------------------------------------------

def parse_readme(content: str) -> list[dict]:
    """
    Parse the SimplifyJobs README (mixed markdown headings + HTML tables)
    and return internship listings for Software Engineering, Product Management,
    and Data Science / AI sections.
    """
    listings = []

    # README uses markdown '## Heading' (not HTML <h2>), so split on those
    sections = re.split(r"^## ", content, flags=re.MULTILINE)

    for section in sections:
        first_line = section.split("\n")[0].strip()

        matched_key = None
        for key in TARGET_SECTIONS:
            if key in first_line:
                matched_key = key
                break
        if not matched_key:
            continue

        emoji, category_label, color = TARGET_SECTIONS[matched_key]

        soup  = BeautifulSoup(section, "html.parser")
        table = soup.find("table")
        if not table:
            continue

        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 4:
                continue

            # ── Company ──────────────────────────────────────────
            company_el   = cells[0].find("a")
            company_name = cells[0].get_text(strip=True).replace("🔥", "").strip()
            company_url  = company_el["href"] if company_el else None

            # ── Role ─────────────────────────────────────────────
            role = cells[1].get_text(strip=True)
            if "🔒" in role or not role or role.startswith("↳"):
                continue
            role = role.replace("🔒", "").replace("🎓", "").strip()

            # ── Location ─────────────────────────────────────────
            location = cells[2].get_text(separator=", ", strip=True) or "Not specified"

            # ── Apply URL ────────────────────────────────────────
            apply_img = cells[3].find("img", alt="Apply")
            if apply_img and apply_img.parent and apply_img.parent.get("href"):
                apply_url = apply_img.parent["href"]
            else:
                first_a = cells[3].find("a")
                if not first_a or not first_a.get("href"):
                    continue
                apply_url = first_a["href"]

            # ── Date / Age ────────────────────────────────────────
            date_posted = cells[4].get_text(strip=True) if len(cells) > 4 else "—"

            listings.append({
                "id":          make_id(company_name, role, apply_url),
                "company":     company_name,
                "company_url": company_url,
                "role":        role,
                "location":    location,
                "apply_url":   apply_url,
                "date_posted": date_posted,
                "category":    f"{emoji} {category_label}",
                "color":       color,
            })

    return listings


# ---------------------------------------------------------------------------
# Discord embed builder
# ---------------------------------------------------------------------------

def build_embed(listing: dict, source_label: str, location_label: str) -> discord.Embed:
    embed = discord.Embed(
        title=f"{listing['company']}  —  {listing['role']}"[:256],
        url=listing["apply_url"],
        color=listing["color"],
        timestamp=datetime.now(timezone.utc),
    )

    embed.add_field(name="📍 Location",  value=listing["location"][:1024], inline=True)
    embed.add_field(name="🗂 Category",  value=listing["category"],         inline=True)
    embed.add_field(name="📅 Posted",    value=listing["date_posted"],      inline=True)
    embed.add_field(
        name="🔗 Apply",
        value=f"[Click here to apply]({listing['apply_url']})",
        inline=False,
    )

    if listing.get("company_url"):
        embed.set_author(name=listing["company"], url=listing["company_url"])
    else:
        embed.set_author(name=listing["company"])

    embed.set_footer(text=f"{location_label}  •  {source_label}")
    return embed


# ---------------------------------------------------------------------------
# Discord bot
# ---------------------------------------------------------------------------

intents = discord.Intents.default()
client  = discord.Client(intents=intents)


@client.event
async def on_ready():
    print(f"✅  Logged in as {client.user}")
    print(f"📡  Checking every {CHECK_INTERVAL_MINS} minute(s)")
    print(f"    Remote     → {CHANNEL_REMOTE}")
    print(f"    California → {CHANNEL_CALIFORNIA}")
    print(f"    Washington → {CHANNEL_WASHINGTON}")
    print(f"    New York   → {CHANNEL_NEW_YORK}")
    print(f"    General    → {CHANNEL_GENERAL}")
    check_and_post.start()


@tasks.loop(minutes=CHECK_INTERVAL_MINS)
async def check_and_post():
    # Build channel lookup — skip any that are set to 0 (not configured)
    channels = {}
    for key, id_fn in CHANNEL_MAP.items():
        ch_id = id_fn()
        if ch_id != 0:
            ch = client.get_channel(ch_id)
            if ch:
                channels[key] = ch
            else:
                print(f"⚠️   Could not find channel for '{key}' (ID {ch_id})")

    if not channels:
        print("❌  No valid channels configured. Check your Railway variables.")
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

        listings     = parse_readme(resp.text)
        new_listings = [l for l in listings if l["id"] not in posted_ids]
        print(f"    {len(listings)} relevant listings found, {len(new_listings)} new.")

        for listing in new_listings:
            loc_key   = get_location_channel(listing["location"])
            channel   = channels.get(loc_key) or channels.get("general")

            if not channel:
                continue  # no matching or general channel configured

            loc_label = CHANNEL_LABELS.get(loc_key, "🌍 Other")

            try:
                embed = build_embed(listing, source["label"], loc_label)
                await channel.send(embed=embed)
                posted_ids.add(listing["id"])
                new_total += 1
                await asyncio.sleep(1.2)
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
        raise SystemExit("❌  DISCORD_TOKEN is not set.")
    client.run(TOKEN)
