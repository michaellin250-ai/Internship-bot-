
Hi, welcome to my project! I built this internship bot because I noticed that a lot of people, including myself 
.Spend a ton of time on Discord. So I figured, why not bring the internship hunt there too? 
Instead of constantly refreshing job boards, this bot automatically pulls new SWE, PM, and AI internship listings 
and delivers them straight to your Discord server, filtered by location so you only see what's relevant to you.

Ultimately it's a Discord bot that automatically monitors the SimplifyJobs Summer 2026 Internships GitHub repository and posts new CS, PM, 
and AI/Data Science internship listings to location-specific channels every 20 minutes.

Features
🔄 Auto-updates every 20 minutes (configurable)
📍 Location-based routing — posts to separate channels for Remote, California, Washington, New York, and General
🗂 Category filtering — covers Software Engineering, Product Management, and Data Science / AI roles
🔁 Duplicate prevention — tracks posted listings via a local JSON file so nothing gets double-posted
📨 Rich embeds — each listing includes company, role, location, category, date posted, and a direct apply link
Setup
1. Clone the repo
bash
git clone https://github.com/michaellin250-ai/discord-internship-bot
cd discord-internship-bot
2. Install dependencies
bash
pip install -r requirements.txt
3. Configure environment variables
Copy .env.example to .env and fill in your values:

bash
cp .env.example .env
env
DISCORD_TOKEN=your_bot_token_here
CHECK_INTERVAL_MINUTES=20

CHANNEL_REMOTE=your_channel_id
CHANNEL_CALIFORNIA=your_channel_id
CHANNEL_WASHINGTON=your_channel_id
CHANNEL_NEW_YORK=your_channel_id
CHANNEL_GENERAL=your_channel_id
How to get channel IDs: Enable Developer Mode in Discord (Settings → Advanced), then right-click any channel and click "Copy Channel ID".

4. Run the bot
bash
python bot.py
Deploying to Railway
Push your repo to GitHub
Create a new project on Railway and connect your repo
Add all environment variables under Variables in your Railway project settings
Railway will auto-deploy — no server needed
Channel Structure
Channel	What gets posted
CHANNEL_REMOTE	Remote / WFH listings
CHANNEL_CALIFORNIA	SF, LA, San Jose, San Diego, etc.
CHANNEL_WASHINGTON	Seattle, Redmond, Bellevue, etc.
CHANNEL_NEW_YORK	NYC, Manhattan, Brooklyn, etc.
CHANNEL_GENERAL	Everything else
Tech Stack
Python — core language
discord.py — bot framework
BeautifulSoup4 — HTML table parsing
Requests — fetching GitHub README content
python-dotenv — environment variable management
Contributing
PRs welcome. If you want to add a new location or job source, edit LOCATION_KEYWORDS and SOURCES in bot.py.



