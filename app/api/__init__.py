from __future__ import annotations
import typing

import asyncio
import contextlib

from aiohttp import web
from . import handlers
from .mobile import handlers as mobile_handlers
from . import cloudpayments


async def run() -> None:
    """
    Starts the server synchronously
    """

    async with contextlib.AsyncExitStack() as stack:
        app = web.Application()
        app.add_routes(handlers.routes)
        app.add_routes(mobile_handlers.routes)
        stack.push_async_callback(app.cleanup)

        await cloudpayments.setup(app)
        stack.push_async_callback(cloudpayments.cleanup)
        
        app.freeze()
        stack.push_async_callback(app.shutdown)

        runner = web.AppRunner(app)
        await runner.setup()
        stack.push_async_callback(runner.cleanup)
        
        site = web.TCPSite(runner, '', 8000)
        await site.start()
        
        # If an exception occurs, we're broken out of this loop
        # and the cleanup handlers run
        while True:
            await asyncio.sleep(60)


__all__ = [
    "run",
]
