import argparse
import asyncio
import logging
import tempfile
from pathlib import Path

from .bot import SignupBot, SignupView
from .config import Settings


async def smoke_test() -> None:
    with tempfile.TemporaryDirectory() as directory:
        bot = SignupBot(Settings(channel_id=1, database_path=Path(directory) / "smoke.db"))
        try:
            commands = sorted(command.qualified_name for command in bot.tree.walk_commands())
            assert commands == ["lock", "mysignups", "signup", "signup post", "signup status", "unlock"]
            for locked in (False, True):
                view = SignupView(bot, "2030-01-01", locked)
                assert view.is_persistent()
                assert [button.disabled for button in view.children] == [locked, locked, False]
            print("Offline smoke test passed: " + ", ".join(commands))
            print("Persistent views built; no token loaded and no Discord connection attempted.")
        finally:
            await bot.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Commander game-night signup bot")
    parser.add_argument("--smoke-test", action="store_true", help="Build the bot and command tree without connecting")
    args = parser.parse_args()
    if args.smoke_test:
        asyncio.run(smoke_test())
        return
    try:
        settings = Settings.from_env()
        if not settings.token or settings.token == "replace_with_your_bot_token":
            raise ValueError("Set DISCORD_TOKEN in your .env or hosting environment.")
    except ValueError as error:
        parser.error(str(error))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    bot = SignupBot(settings)
    bot.run(settings.token, log_handler=None)


if __name__ == "__main__":
    main()
