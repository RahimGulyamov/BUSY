from __future__ import annotations

import typing
import argparse
import pathlib
import asyncio
import warnings
import logging
import traceback
import shlex
import sys

from .config_helper import import_config
from .log import setup_logging

from . import api, db, telegram, voximplant, scheduler, common, amoCRM, pymorphy2


parser = argparse.ArgumentParser(
    description="The back-end for the Busy server.",
)

parser.add_argument(
    "--config", "-c",
    type=pathlib.Path,
    required=True,
    help="The path to the `config.py` file.",
)

parser.add_argument(
    "--test-run",
    action="store_true",
    help="Run the debug code."
)

parser.add_argument(
    "--no-api",
    action="store_true",
    help="Don't start the API server.",
)

parser.add_argument(
    "--no-tg",
    action="store_true",
    help="Don't start the Telegram bot.",
)

parser.add_argument(
    "--no-vox",
    action="store_true",
    help="Don't start the voximplant client.",
)

parser.add_argument(
    "--no-amo",
    action="store_true",
    help="Don't add amoCRM integration.",
)

parser.add_argument(
    "--reset-default-prefs",
    action=argparse.BooleanOptionalAction,
    # default=True,
    help="Reset the default preferences in the database to the hardcoded ones.",
)

parser.add_argument(
    "--reset-plans",
    action=argparse.BooleanOptionalAction,
    # default=True,
    help="Overwrite the plans in the database with hardcoded ones, rather than just check them.",
)


async def run_module(
    name: str,
    run_coro: typing.Awaitable[None],
) -> None:
    logging.info(f"Starting {name}...")
    try:
        await run_coro
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit) as e:
        logging.info(f"Graceful shutdown for {name} ({type(e).__qualname__})")
        raise
    except:
        logging.exception(f"{name} terminated due to an unhandled exception.")
        raise
    else:
        logging.info(f"{name} successfully completed.")


async def test_run() -> None:
    from .test_run import test_run
    
    logging.info("Performing test run.")
    
    await test_run()


def set_log_level() -> None:
    import config
    
    logger: logging.Logger = logging.getLogger()
    logger.setLevel(config.LOG_LEVEL)


async def reset_default_prefs() -> None:
    async with db.DatabaseApi().session():
        await common.reset_global_config()
    logging.info("Default preferences reset.")


async def reset_plans() -> None:
    async with db.DatabaseApi().session():
        await common.create_plans()
    logging.info("Plans reset.")


async def validate_plans() -> None:
    async with db.DatabaseApi().session():
        if await common.validate_plans():
            logging.info("Plans verified.")
            return
    
    raise ValueError("Mismatch between expected and actual plans!")


async def main(args: argparse.Namespace) -> None:
    try:
        import_config(args.config)

        tasks: list[typing.Coroutine] = []
        
        set_log_level()

        # Must be done before reset_default_prefs
        pymorphy2.setup()
        
        if args.reset_default_prefs:
            await reset_default_prefs()
        
        if args.reset_plans:
            await reset_plans()
        await validate_plans()

        if args.test_run:
            await test_run()
            return

        if not args.no_api:
            # Start the API server
            tasks.append(run_module("API server", api.run()))
        
        if not args.no_tg:
            # Start the telegram bot
            tasks.append(run_module("Telegram bot", telegram.run()))

        if not args.no_vox:
            # Start the voximplant client
            tasks.append(run_module("Voximplant client", voximplant.run()))

        if not args.no_amo:
            # Start the amoCRM client
            tasks.append(run_module("AmoCRM client", amoCRM.run()))

        # Start the scheduler
        tasks.append(run_module("Scheduler", scheduler.run()))
        
        await asyncio.shield(asyncio.gather(*tasks))
    except KeyboardInterrupt:
        logging.info("Keyboard interrupt received. Shutting down.")
        return
    except SystemExit:
        logging.info("System exit triggered. Shutting down.")
        return
    finally:
        await db.DatabaseApi().dispose()


if __name__ == "__main__":
    setup_logging()
    
    logging.info(f"Staring with args: {shlex.join(sys.argv[1:])}")
    
    args: argparse.Namespace = parser.parse_args()
    
    # Note: not `asyncio.run` because see here: https://stackoverflow.com/questions/65682221
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        loop.run_until_complete(main(args))
    except (KeyboardInterrupt, SystemExit):
        logging.info("Graceful shut down.")
    except:
        logging.exception("Unhandled exception.")
