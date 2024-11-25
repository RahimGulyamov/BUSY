from sqlalchemy import select

import app.api
import app.telegram
import app.db
from app.config_helper import import_config
import logging
import argparse
import asyncio

from app.db import User
from app.log import setup_logging
from aiogram.contrib.fsm_storage.redis import RedisStorage2
from aiogram.contrib.fsm_storage.memory import MemoryStorage
import shlex
import sys
import pathlib
import sqlalchemy

parser = argparse.ArgumentParser(
    description="The back-end for the Busy server.",
)

parser.add_argument(
    "--config", "-c",
    type=pathlib.Path,
    required=True,
    help="The path to the `config.py` file.",
)


async def main(args: argparse.Namespace) -> None:
    import_config(args.config)
    import config
    if config.REDIS_URL is None:
        redis_storage = MemoryStorage()
    else:
        redis_storage = RedisStorage2(config.REDIS_URL, config.REDIS_PORT, db=5, pool_size=10, prefix='my_fsm_key')
    async with app.db.DatabaseApi().session() as session:
        user_ids = (
            await session.scalars(
                select(User.telegram_id)
                .where(User.telegram_id.is_not(None))
            )
        ).all()

    print(f'collected {user_ids=}')
    async with app.db.DatabaseApi().session():
        storage = app.db.DatabaseStorage()
        for user_id in user_ids:
            try:
                state = await redis_storage.get_state(chat=user_id, user=user_id)
                data = await redis_storage.get_data(chat=user_id, user=user_id)
                await storage.set_state(user=user_id, chat=user_id, state=state)
                await storage.set_data(user=user_id, chat=user_id, data=data)
            except Exception as e:
                print(f'while syncing {user_id=} an error occurred {e}')
            else:
                print(f'{user_id=} proccessed successfully')
    await redis_storage.close()
    await storage.close()

    print("Done.")


if __name__ == "__main__":
    setup_logging()
    logging.info(f"Staring with args: {shlex.join(sys.argv[1:])}")
    args: argparse.Namespace = parser.parse_args()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main(args))
    except (KeyboardInterrupt, SystemExit):
        logging.info("Graceful shut down.")
    except:
        logging.exception("Unhandled exception.")
