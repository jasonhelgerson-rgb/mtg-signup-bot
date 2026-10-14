# Commander signup bot

A small Python 3.11+ Discord bot for one server and one signup text channel.
Each date has one pod, ordered backups, and flexible availability that holds no
seat. SQLite stores signups, lock state, message IDs, and pending notifications.

## Create and invite the bot

1. Open the [Discord Developer Portal](https://discord.com/developers/applications).
   Choose **New Application**, give it a name, and create it.
2. Open **Bot**. Add a bot if the portal offers **Add Bot**; otherwise use the bot
   already associated with the application. Use **Reset Token** to copy its token.
   Put it in your local `.env` or Railway's Variables privately. Never put it in
   GitHub, a screenshot, or a chat message. Disable **Public Bot** if you only
   want to install it in your own server.
3. Leave **Presence Intent**, **Server Members Intent**, and **Message Content
   Intent** off. No privileged intents are required. The code enables only the
   ordinary Guilds intent; slash commands and button interactions supply user IDs.
   See [Discord's gateway intents](https://docs.discord.com/developers/events/gateway#gateway-intents).
4. Under **OAuth2 → URL Generator**, choose scopes **`bot`** and
   **`applications.commands`**. Choose these bot permissions:
   - **View Channel**
   - **Send Messages**
   - **Embed Links**
   - **Read Message History**
5. Open the generated invite URL, select your server, and authorize it. If an
   installation type is offered, use **Guild Install / server installation**.
   Check that channel overrides also allow those four permissions in the signup
   channel. Players need **Use Application Commands** to run `/mysignups`.

The invite's permission integer is `84992`. You can also replace `YOUR_APP_ID`
in this URL with the Application ID from General Information:

```text
https://discord.com/oauth2/authorize?client_id=YOUR_APP_ID&permissions=84992&scope=bot%20applications.commands
```

Notifications are ordinary messages in the signup channel. Threads are not used,
so Create Public Threads and Send Messages in Threads are unnecessary. Mention
Everyone and Administrator are unnecessary. Only the individual seated or
promoted players are allowed to receive notification pings.

## Get the channel and server IDs

In Discord, open **User Settings → Advanced → Developer Mode** and turn it on.
Right-click your signup text channel and select **Copy Channel ID**. Use that
number as `SIGNUP_CHANNEL_ID`. On mobile, use the channel's long-press menu.

Optionally, right-click the server icon and use **Copy Server ID** for `GUILD_ID`.
Use a normal server text channel, rather than a forum channel or thread.

## Run locally

Extract the zip, then work inside its `mtg-signup-bot` folder. Install Python
3.11 or later and run:

```sh
cd mtg-signup-bot
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

On Windows, create the environment with `py -3.11 -m venv .venv`, activate it
with `.venv\Scripts\Activate.ps1`, and copy `.env.example` to `.env` with your
editor or `Copy-Item .env.example .env`.

Edit `.env`: set `DISCORD_TOKEN`, `SIGNUP_CHANNEL_ID`, and preferably `GUILD_ID`.
Start from this folder so `.env` and relative database paths resolve correctly:

```sh
python -m mtg_signup_bot
```

The console should report a connection, and the channel should receive upcoming
signup posts. `GUILD_ID` registers commands in that server for fast availability.
Without it, commands are registered globally and Discord may take time to show
them. Use one registration mode consistently to avoid old global and server
versions appearing together.

Only run **one bot process / one Railway replica** for this project. Stop a local
copy before starting the hosted copy with the same token and database. Posting
and allocation are serialized within that one process.

### Configuration

Environment variables supplied by the host take precedence over `.env` values.

| Variable | Default | Meaning |
| --- | --- | --- |
| `DISCORD_TOKEN` | Required | Your bot's private token |
| `SIGNUP_CHANNEL_ID` | Required | Server text channel for posts and notifications |
| `GUILD_ID` | Unset | Optional server ID for fast command registration |
| `DATABASE_PATH` | `./data/signups.db` | SQLite file; its parent directory is created automatically |
| `TIMEZONE` | `America/Los_Angeles` | IANA timezone used for dates and scheduling |
| `LOCK_TIME` | `15:00` | Local 24-hour `HH:MM` lock time |
| `POD_SIZE` | `4` | Seats in the single pod; supported range 1–20 |
| `DAYS_AHEAD` | `7` | Number of posted calendar dates, starting today; range 1–31 |

All seven weekdays are supported. The default window is **today through six days
from today**, including today's locked post after 3 PM. Startup and a once-a-minute
loop create missing posts, check saved message IDs, and lock due nights. The loop
may lock up to a minute after the scheduled time; a button click also checks the
deadline before accepting a new signup. Missed locks are processed in date order
after a restart.

`zoneinfo` applies Pacific standard/daylight offsets, rather than a fixed UTC
offset. Embeds show the deadline as a Discord timestamp in each viewer's local
time. If a custom lock time falls in a spring-forward gap it moves forward by
the gap; during a repeated fall-back hour the first occurrence is used. `tzdata`
is included for machines without a system timezone database.

## Deploy on Railway

This is a continuously running **worker**. It makes an outbound Discord
connection and needs no web port, domain, `PORT` variable, or HTTP health check.
Keep serverless/sleeping behavior disabled so it remains connected.

The supplied `railway.json` chooses Railpack and sets
`python -m mtg_signup_bot` as the start command. `Procfile` also declares a
`worker` process. `.python-version` requests Python 3.11; you can set
`RAILPACK_PYTHON_VERSION=3.11` in Railway explicitly.
See [Railway start commands](https://docs.railway.com/deployments/start-command)
and [Railpack Python configuration](https://railpack.com/languages/python/).

### Option A: your own GitHub repository

1. Create a new GitHub repository for this standalone project. Upload the contents
   of the extracted folder so `README.md`, `requirements.txt`, and `railway.json`
   are at the repository root. Exclude `.env`, databases, and `.venv` (the provided
   `.gitignore` covers them). No Discord monorepo checkout is needed.
2. In Railway, choose **New Project → Deploy from GitHub repo**, authorize access
   if needed, and select this repository. Railway detects `requirements.txt` and
   installs the runtime dependencies. An initial deployment can fail until the
   required variables are configured; complete the next steps before redeploying.
3. In the service's **Variables**, add:
   - `DISCORD_TOKEN`: paste your real bot token privately here.
   - `SIGNUP_CHANNEL_ID`: the target channel's numeric ID.
   - `GUILD_ID`: recommended, your server's numeric ID.
   - `DATABASE_PATH=/data/signups.db`.
   - `TIMEZONE=America/Los_Angeles`, `LOCK_TIME=15:00`, `POD_SIZE=4`,
     and `DAYS_AHEAD=7`, or your preferred values.
   - Optionally, `RAILPACK_PYTHON_VERSION=3.11`.
4. **Add a Volume** to this bot service and set its mount path to **`/data`**.
   Use the project canvas's right-click menu or Command Palette → Create Volume,
   select the bot service, and configure the mount path. This must be a persistent
   Railway Volume attached to the running service. Setting `DATABASE_PATH` alone
   does not preserve data across redeploys. See [Railway Volumes](https://docs.railway.com/volumes).
5. Check service settings: use **one replica**, leave the HTTP Healthcheck Path
   empty, and set the Start Command to **`python -m mtg_signup_bot`** if it is not
   already loaded from `railway.json`. Deploy the pending configuration changes
   or redeploy the service. The volume is used at runtime, so no pre-deploy database
   step is needed.
6. In runtime logs, look for **Connected as …; signup channel …**. Confirm the
   rolling posts and `/signup status` in Discord. Then do a controlled redeploy:
   the same signup posts and stored signups should survive. Do not add a public
   domain to make this worker run.

### Option B: Railway CLI upload

You do not need to publish the code on GitHub. In the Railway dashboard create an
empty project and an empty service, add its `/data` Volume, and set the variables
from Option A first. Install the [Railway CLI](https://docs.railway.com/cli), then:

```sh
cd mtg-signup-bot
railway login
railway link
railway up
```

Select your project, environment, and bot service when prompted by `railway link`.
`railway up` uploads this directory and deploys it. The CLI respects `.gitignore`,
so your local `.env`, database, and virtual environment stay out of the upload.
Read [Railway's CLI deployment guide](https://docs.railway.com/cli/deploying).
Check the same start command, replica count, runtime logs, and Discord behavior
as in Option A.

### Persistence and restarts

Keep `/data` mounted and `DATABASE_PATH=/data/signups.db` unchanged between
deployments. Back up the Volume using Railway's volume backup features. For a
manual database copy, stop the worker first and copy `signups.db`.

Saved IDs prevent ordinary reposts on restart. If the bot stops between Discord
accepting a signup message and SQLite recording its ID, it searches channel
history for its dated embed before sending again. Deleted upcoming posts are
recreated with the same signups. Do not manually copy or edit the bot's dated
embed footer; it is its recovery marker.

Allocation and notification records are committed together. Failed notification
sends are retried. A crash after Discord accepts a notification but before its
acknowledgment is saved can repeat that notification; this does not repeat the
allocation or reserve an extra seat.

## How players use it

- **Join** is a firm commitment to that date. It takes the next seat, or appends
  you to the ranked backups if the pod is full. Repeated clicks do not change your
  rank. Joining removes your Flex entry for that same date.
- **Flex** says this is one of your possible dates and holds no seat. Players who
  can play only one flexible night should use Flex on all their options. To change
  an existing Join/backup entry to Flex, click Leave first.
- **Leave** removes your seat, backup, or flex entry. If you held a seat, the next
  backup is promoted and pinged. Before lock, flex players wait until lock to be
  allocated.
- At lock, existing seats stay in order. Empty seats go to backups first, then
  flex players in the order they clicked Flex. When a flex player receives a seat,
  their Flex entries on **all other dates** are removed and those embeds update.
  The channel gets a summary pinging seated players, reporting any shortage, and
  listing remaining standby backups.
- After lock, **Join** and **Flex** are disabled. **Leave** still works. A late
  seat dropout promotes a backup, then the earliest remaining flex player if no
  backups are available; that flex promotion also clears other flex options.
- Button confirmations are private. Use **`/mysignups`** for a private list of
  your seats, backup ranks, and flex choices in the upcoming window.

Explicit Joins on different nights are separate commitments; this bot does not
enforce a weekly cap on them. Flex allocation removes other Flex options, not
explicit seats/backups on other dates. Leaving an allocated seat does not restore
previously removed flex choices. A full pod never absorbs extra players into seats.
Very long backup/flex lists are shortened to fit Discord's embed limits, with
the full counts shown; all entries and their ranks remain stored.

## Admin commands

These require **Manage Server OR Manage Events** in the configured server. The
checks run in the bot; users without either permission may see the command but
cannot execute it. `/mysignups` is available to everyone in that server.

| Command | What it does |
| --- | --- |
| `/signup post date:2030-01-07` | Create a date's post, or return its existing post |
| `/signup post date:2030-01-07 repost:True` | Delete and replace its post; preserve signups and lock state |
| `/lock date:2030-01-07` | Lock early, allocate open seats, and post the summary |
| `/unlock date:2030-01-07` | Reopen without undoing allocation or restoring old flex options |
| `/signup status` | Privately show upcoming nights, seat totals, backups, and flex counts |

Dates must be `YYYY-MM-DD` in the configured timezone, from today up to one year
ahead. Replace the examples with an actual upcoming date. Post a date before
using `/lock` or `/unlock` on it. Manually posted dates beyond the rolling window
still lock automatically when due; they enter the status/mysignups window later.

Reopening **before** its scheduled time leaves the automatic lock in place.
Reopening **after** its scheduled time stays open until you use `/lock` again,
including across restarts. This prevents the maintenance loop immediately
locking an admin-reopened night. Today's post created after lock time will lock
immediately; use `/unlock` if you want late signups.

## Tests and offline smoke check

```sh
pip install -r requirements-dev.txt
python -m pytest -q
python -m mtg_signup_bot --smoke-test
```

The smoke check uses a temporary database and ignores `.env`; it imports the bot,
builds the slash-command tree, and checks persistent views without a token or
Discord connection. Tests include allocation order, overflow, promotions, flex
cleanup across dates, short nights, DST, SQLite recovery, and mocked Discord
posting/interactions. No tests connect to Discord or deploy to Railway. After
hosting, use a test date to verify buttons, pings, permissions, and a restart in
your own server.

## Code map

```text
mtg-signup-bot/
├── .env.example
├── .gitignore
├── .python-version
├── Procfile
├── README.md
├── railway.json
├── requirements.txt
├── requirements-dev.txt
├── mtg_signup_bot/
│   ├── __init__.py
│   ├── __main__.py
│   ├── allocation.py
│   ├── bot.py
│   ├── config.py
│   └── storage.py
└── tests/
    ├── test_allocation.py
    ├── test_bot.py
    └── test_storage_config.py
```

`allocation.py` has immutable night data and pure rules. `bot.py` handles Discord
and scheduling; `storage.py` handles SQLite; `config.py` handles environment
settings and calendar/timezone calculations. The pure rules are the seam for
future allocation changes.

## Future ideas

- Splitting 6+ signups into two 3-player pods. This is deliberately not implemented;
  the current model is one pod per night, with overflow remaining backups.
