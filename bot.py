"""Discord commands, persistent buttons, and the once-a-minute scheduler."""

import asyncio
import logging
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import tasks

from . import allocation
from .allocation import Change, Night
from .config import Settings, parse_day
from .storage import Notice, Store

log = logging.getLogger(__name__)


def marker(day: str) -> str:
    return f"MTG signup | {day}"


def ranked(players: tuple[int, ...]) -> str:
    if not players:
        return "None"
    lines = []
    for position, user in enumerate(players, 1):
        line = f"{position}. <@{user}>"
        if sum(len(item) + 1 for item in lines) + len(line) > 900:
            lines.append(f"… and {len(players) - position + 1} more.")
            break
        lines.append(line)
    return "\n".join(lines)


def night_embed(settings: Settings, day: str, night: Night, *, auto_lock_done: bool = False) -> discord.Embed:
    scheduled = int(settings.lock_at(parse_day(day)).timestamp())
    status = "Locked" if night.locked else "Open"
    description = f"**{status}** · Scheduled lock: <t:{scheduled}:F> (<t:{scheduled}:R>)"
    if auto_lock_done and not night.locked:
        description += "\nReopened by an admin; will stay open until /lock is used."
    embed = discord.Embed(
        title=f"Commander · {parse_day(day).strftime('%A, %B %d, %Y')}",
        description=description,
        color=discord.Color.orange() if night.locked else discord.Color.green(),
    )
    seat_lines = [f"{number}. <@{user}>" for number, user in enumerate(night.seats, 1)]
    seat_lines += [f"{number}. — Open seat" for number in range(len(night.seats) + 1, settings.pod_size + 1)]
    embed.add_field(name=f"Seats ({len(night.seats)}/{settings.pod_size})", value="\n".join(seat_lines) or "None", inline=False)
    embed.add_field(name=f"Ranked Backups ({len(night.backups)})", value=ranked(night.backups), inline=False)
    embed.add_field(name=f"Flex options ({len(night.flex)})", value=ranked(night.flex), inline=False)
    embed.set_footer(text=marker(day))
    return embed


def notices(title: str, lines: list[str], ping_ids: tuple[int, ...]) -> list[Notice]:
    chunks = []
    content = title
    for line in lines:
        if len(content) + len(line) + 1 > 1900:
            chunks.append(content)
            content = title + " (continued)"
        content += "\n" + line
    chunks.append(content)
    return [Notice(chunk, tuple(user for user in ping_ids if f"<@{user}>" in chunk)) for chunk in chunks]


def lock_notices(day: str, night: Night, pod_size: int) -> list[Notice]:
    lines = [f"Seat {position}: <@{user}>" for position, user in enumerate(night.seats, 1)]
    short = max(0, pod_size - len(night.seats))
    lines.append(f"Still short by {short} player(s)." if short else "The pod is full.")
    if night.backups:
        lines.append("Backups on standby:")
        lines += [f"{position}. <@{user}>" for position, user in enumerate(night.backups, 1)]
    else:
        lines.append("No backups on standby.")
    return notices(f"🔒 Commander {day} is locked.", lines, night.seats)


async def respond(interaction: discord.Interaction, content: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(content, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    else:
        await interaction.response.send_message(content, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


async def defer_private(interaction: discord.Interaction) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)


async def report_error(interaction: discord.Interaction, error: Exception) -> None:
    cause = error.original if isinstance(error, app_commands.CommandInvokeError) else error
    if isinstance(cause, (ValueError, app_commands.CheckFailure)):
        message = str(cause)
    else:
        log.error("Discord interaction failed", exc_info=(type(cause), cause, cause.__traceback__))
        message = "Couldn't finish this action. Try again or ask the host to check the bot logs."
    await respond(interaction, message)


class SignupView(discord.ui.View):
    def __init__(self, bot: "SignupBot", day: str, locked: bool):
        super().__init__(timeout=None)
        self.bot = bot
        self.day = day
        for label, action, style in (
            ("Join", "join", discord.ButtonStyle.success),
            ("Flex", "flex", discord.ButtonStyle.primary),
            ("Leave", "leave", discord.ButtonStyle.secondary),
        ):
            button = discord.ui.Button(
                label=label, style=style, custom_id=f"mtg:{day}:{action}", disabled=locked and action != "leave",
            )

            async def callback(interaction: discord.Interaction, action: str = action) -> None:
                await self.bot.button_action(interaction, self.day, action)

            button.callback = callback
            self.add_item(button)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        await report_error(interaction, error)


class SignupTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        await defer_private(interaction)
        await self.client.check_server(interaction)
        return True

    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        await report_error(interaction, error)


class SignupBot(discord.Client):
    def __init__(self, settings: Settings):
        intents = discord.Intents.none()
        intents.guilds = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.settings = settings
        self.store = Store(settings.database_path)
        self.gate = asyncio.Lock()
        self.dirty: set[str] = set()
        self._channel: discord.TextChannel | None = None
        self.tree = SignupTree(self)
        self._build_commands()

    async def setup_hook(self) -> None:
        for record in self.store.records():
            if record.message_id:
                self.add_view(SignupView(self, record.day, record.night.locked), message_id=record.message_id)
                if parse_day(record.day) >= self.settings.now().date():
                    self.dirty.add(record.day)
        if self.settings.guild_id:
            guild = discord.Object(id=self.settings.guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        self.reconcile.start()

    async def close(self) -> None:
        if self.reconcile.is_running():
            task = self.reconcile.get_task()
            self.reconcile.cancel()
            if task:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        await super().close()
        self.store.close()

    async def on_ready(self) -> None:
        log.info("Connected as %s; signup channel %s", self.user, self.settings.channel_id)

    async def signup_channel(self) -> discord.TextChannel:
        if self._channel is None:
            channel = self.get_channel(self.settings.channel_id) or await self.fetch_channel(self.settings.channel_id)
            if not isinstance(channel, discord.TextChannel):
                raise ValueError("SIGNUP_CHANNEL_ID must refer to a server text channel.")
            if self.settings.guild_id and channel.guild.id != self.settings.guild_id:
                raise ValueError("GUILD_ID does not match the signup channel's server.")
            self._channel = channel
        return self._channel

    async def check_server(self, interaction: discord.Interaction) -> None:
        channel = await self.signup_channel()
        if interaction.guild_id != channel.guild.id:
            raise app_commands.CheckFailure("Use this bot in the server containing the signup channel.")

    async def admin_check(self, interaction: discord.Interaction) -> bool:
        permissions = interaction.permissions
        if not (permissions.manage_guild or permissions.manage_events):
            raise app_commands.CheckFailure("You need Manage Server or Manage Events permission for this command.")
        return True

    def command_day(self, value: str) -> str:
        day = parse_day(value)
        today = self.settings.now().date()
        if not today <= day <= today + timedelta(days=365):
            raise ValueError("Choose today or a future date within the next year, in the configured timezone.")
        return day.isoformat()

    def save_change(self, change: Change, queued: list[Notice] | None = None, *, auto_done: tuple[str, ...] = ()) -> None:
        before = self.store.states()
        self.store.commit(change.nights, queued or (), auto_done=auto_done)
        self.dirty.update(day for day, night in change.nights.items() if before.get(day) != night)

    def lock_due(self) -> None:
        now = self.settings.now()
        for record in self.store.records():
            if record.night.locked or record.auto_lock_done or now.timestamp() < self.settings.lock_at(parse_day(record.day)).timestamp():
                continue
            states = self.store.states()
            change = allocation.lock(states, record.day, self.settings.pod_size)
            self.save_change(change, lock_notices(record.day, change.nights[record.day], self.settings.pod_size), auto_done=(record.day,))

    async def ensure_post(self, day: str, *, repost: bool = False) -> discord.Message:
        channel = await self.signup_channel()
        record = self.store.ensure(day)
        if record.message_id:
            try:
                if repost:
                    await channel.get_partial_message(record.message_id).delete()
                    self.store.set_message(day, None)
                else:
                    return await channel.fetch_message(record.message_id)
            except discord.NotFound:
                self.store.set_message(day, None)
        record = self.store.get(day)
        # Recover a successful send whose message ID was not saved before a crash.
        async for message in channel.history(limit=None, after=record.created_at - timedelta(seconds=5)):
            if message.author.id == self.user.id and any(embed.footer.text == marker(day) for embed in message.embeds):
                self.store.set_message(day, message.id)
                self.dirty.add(day)
                return message
        message = await channel.send(
            embed=night_embed(self.settings, day, record.night, auto_lock_done=record.auto_lock_done),
            view=SignupView(self, day, record.night.locked),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.store.set_message(day, message.id)
        self.dirty.discard(day)
        return message

    async def sync_discord(self) -> bool:
        channel = await self.signup_channel()
        for day in sorted(self.dirty):
            record = self.store.get(day)
            if record.message_id is None:
                if parse_day(day) < self.settings.now().date():
                    self.dirty.discard(day)
                    continue
                try:
                    await self.ensure_post(day)
                    record = self.store.get(day)
                except discord.HTTPException:
                    log.exception("Could not restore signup post for %s; will retry", day)
                    continue
            try:
                await channel.get_partial_message(record.message_id).edit(
                    embed=night_embed(self.settings, day, record.night, auto_lock_done=record.auto_lock_done),
                    view=SignupView(self, day, record.night.locked), allowed_mentions=discord.AllowedMentions.none(),
                )
                self.dirty.discard(day)
            except discord.NotFound:
                self.store.set_message(day, None)
                if parse_day(day) >= self.settings.now().date():
                    try:
                        await self.ensure_post(day)
                    except discord.HTTPException:
                        log.exception("Could not restore signup post for %s", day)
                else:
                    self.dirty.discard(day)
            except discord.HTTPException:
                log.exception("Could not update signup post for %s; will retry", day)
        for notice_id, notice in self.store.pending_notices():
            try:
                await channel.send(
                    notice.content,
                    allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=[discord.Object(id=user) for user in notice.user_ids]),
                )
                self.store.acknowledge(notice_id)
            except discord.HTTPException:
                log.exception("Could not send signup notice; will retry")
                break
        return not self.dirty and not self.store.pending_notices()

    @tasks.loop(seconds=60)
    async def reconcile(self) -> None:
        try:
            async with self.gate:
                days = [day.isoformat() for day in self.settings.upcoming()]
                for day in days:
                    self.store.ensure(day)
                self.lock_due()
                for day in days:
                    try:
                        await self.ensure_post(day)
                    except discord.HTTPException:
                        log.exception("Could not post or check signup for %s; will retry", day)
                await self.sync_discord()
        except Exception:
            log.exception("Signup maintenance failed; will retry in one minute")

    @reconcile.before_loop
    async def before_reconcile(self) -> None:
        await self.wait_until_ready()

    async def button_action(self, interaction: discord.Interaction, day: str, action: str) -> None:
        await defer_private(interaction)
        await self.check_server(interaction)
        async with self.gate:
            self.lock_due()
            try:
                record = self.store.get(day)
                if not interaction.message or interaction.message.id != record.message_id:
                    raise ValueError("This post has been replaced. Use the latest signup post in the channel.")
                if parse_day(day) < self.settings.now().date():
                    raise ValueError("This game night has passed. Use an upcoming signup post.")
                states = self.store.states()
                if action == "join":
                    change = allocation.join(states, day, interaction.user.id, self.settings.pod_size)
                elif action == "flex":
                    change = allocation.flex(states, day, interaction.user.id)
                elif action == "leave":
                    change = allocation.leave(states, day, interaction.user.id, self.settings.pod_size)
                else:
                    raise ValueError("Unknown signup action.")
                queued = notices(
                    f"Commander {day}: a seat opened up.",
                    [f"<@{user}> is promoted to a seat!" for user in change.promoted], change.promoted,
                ) if change.promoted else []
                self.save_change(change, queued)
            except ValueError:
                await self.sync_discord()
                raise
            synced = await self.sync_discord()
        suffix = "" if synced else " Your choice is saved; Discord updates will retry shortly."
        await respond(interaction, change.confirmation + suffix)

    async def weekly_response(self, interaction: discord.Interaction, *, user_id: int | None = None) -> None:
        await defer_private(interaction)
        async with self.gate:
            self.lock_due()
            states = self.store.states()
            await self.sync_discord()
            lines = []
            for day in self.settings.upcoming():
                key = day.isoformat()
                night = states.get(key, Night())
                status = "Locked" if night.locked else "Open"
                if user_id is None:
                    lines.append(f"**{key} ({day.strftime('%a')})** · {status} · {len(night.seats)}/{self.settings.pod_size} seats · {len(night.backups)} backups · {len(night.flex)} flex")
                else:
                    for label, players in (("Seat", night.seats), ("Backup", night.backups), ("Flex", night.flex)):
                        if user_id in players:
                            lines.append(f"**{key} ({day.strftime('%a')})** · {label} #{players.index(user_id) + 1} · {status}")
        title = f"Your signups for the next {self.settings.days_ahead} days" if user_id else f"Upcoming {self.settings.days_ahead} days"
        for notice in notices(title, lines or ["You have no signups in this window."], ()):
            await respond(interaction, notice.content)

    def _build_commands(self) -> None:
        signup = app_commands.Group(name="signup", description="Manage Commander signup nights", guild_only=True)

        @signup.command(name="post", description="Post a night, or replace its post while keeping signups")
        @app_commands.describe(date="YYYY-MM-DD in the bot's timezone", repost="Delete the existing signup post and replace it")
        @app_commands.check(self.admin_check)
        async def post(interaction: discord.Interaction, date: str, repost: bool = False) -> None:
            await defer_private(interaction)
            day = self.command_day(date)
            async with self.gate:
                self.store.ensure(day)
                self.lock_due()
                message = await self.ensure_post(day, repost=repost)
                await self.sync_discord()
            await respond(interaction, f"Signup post for {day}: {message.jump_url}")

        @signup.command(name="status", description="Show upcoming game nights at a glance")
        @app_commands.check(self.admin_check)
        async def status(interaction: discord.Interaction) -> None:
            await self.weekly_response(interaction)

        self.tree.add_command(signup)

        @self.tree.command(name="lock", description="Lock a game night and allocate empty seats")
        @app_commands.guild_only()
        @app_commands.describe(date="YYYY-MM-DD in the bot's timezone")
        @app_commands.check(self.admin_check)
        async def lock_command(interaction: discord.Interaction, date: str) -> None:
            await defer_private(interaction)
            day = self.command_day(date)
            async with self.gate:
                self.store.get(day)
                self.lock_due()
                states = self.store.states()
                change = allocation.lock(states, day, self.settings.pod_size)
                queued = [] if states[day].locked else lock_notices(day, change.nights[day], self.settings.pod_size)
                self.save_change(change, queued)
                synced = await self.sync_discord()
            await respond(interaction, change.confirmation + ("" if synced else " Saved; Discord updates will retry shortly."))

        @self.tree.command(name="unlock", description="Reopen a game night without changing existing seats")
        @app_commands.guild_only()
        @app_commands.describe(date="YYYY-MM-DD in the bot's timezone")
        @app_commands.check(self.admin_check)
        async def unlock_command(interaction: discord.Interaction, date: str) -> None:
            await defer_private(interaction)
            day = self.command_day(date)
            async with self.gate:
                self.store.get(day)
                change = allocation.unlock(self.store.states(), day)
                passed = self.settings.now().timestamp() >= self.settings.lock_at(parse_day(day)).timestamp()
                self.save_change(change, auto_done=(day,) if passed else ())
                self.dirty.add(day)
                synced = await self.sync_discord()
            note = " Use /lock to lock it again." if passed else " It will lock automatically at the scheduled time."
            await respond(interaction, change.confirmation + note + ("" if synced else " Saved; Discord updates will retry shortly."))

        @self.tree.command(name="mysignups", description="Privately show your upcoming seats, backups, and flex choices")
        @app_commands.guild_only()
        async def mysignups(interaction: discord.Interaction) -> None:
            await self.weekly_response(interaction, user_id=interaction.user.id)
