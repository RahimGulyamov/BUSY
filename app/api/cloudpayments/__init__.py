from __future__ import annotations
import typing
import logging
import os

from aiocloudpayments.dispatcher.aiohttp_dispatcher import NOTIFICATION_TYPES
from aiocloudpayments.utils.hmac_check import hmac_check
from aiohttp import web
from aiocloudpayments import AiohttpDispatcher, AioCpClient, Result

from . import methods

CP_NOTIFICATION_PATH: str = "/cloudpayments"


class BusyCPDispatcher(AiohttpDispatcher):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def setup(self, app: web.Application, cp_client: AioCpClient):
        # Just a bit of copypaste from base class
        # CP notification handler setup

        # TODO: FIX
        self.ip_whitelist = frozenset({"172.17.0.1"})
        # self.ip_whitelist = frozenset({"127.0.0.1", "130.193.70.192",
        #                                "185.98.85.109", "91.142.84.0/27",
        #                                "87.251.91.160/27", "185.98.81.0/28"}),
        self.cp_client = cp_client
        self.check_hmac = True
        self.register_app(
            app, CP_NOTIFICATION_PATH,
            pay_path="/pay",
            cancel_path="/cancel",
            check_path="/check",
            confirm_path="/confirm",
            fail_path="/fail",
            recurrent_path="/recurrent",
            refund_path="/refund",
        )

    async def process_request(self, request: web.Request) -> web.Response:
        logger = logging.getLogger("aiocloudpayments.dispatcher")

        if self.ip_whitelist and request.remote not in self.ip_whitelist and "0.0.0.0" not in self.ip_whitelist:
            logger.warning(f"skip request from ip {request.remote} because it is not in ip_whitelist")
            return web.json_response(status=401)
        if self.check_hmac is True and hmac_check(
                await request.read(),
                self.cp_client._api_secret,
                request.headers.get("Content-HMAC")) is False:
            logger.warning(f"skip request from because hmac check failed: {request} from {request.remote}")
            return web.json_response(status=401)

        name = self._web_paths[request.url.name]
        notification_type = NOTIFICATION_TYPES.get(name)
        if notification_type is None:
            logger.error(f"notification type {name} not supported")
            return web.json_response(status=500)
        notification = notification_type(**(await request.post()))
        result = await self.process_notification(notification)
        if result == Result.INTERNAL_ERROR:
            return web.json_response(status=500)
        if result:
            return web.json_response({"code": result.value})

    async def process_request(self, request: web.Request) -> web.Response:
        logger = logging.getLogger("aiocloudpayments.dispatcher")

        if self.ip_whitelist and request.remote not in self.ip_whitelist and "0.0.0.0" not in self.ip_whitelist:
            logger.warning(f"skip request from ip {request.remote} because it is not in ip_whitelist")
            return web.json_response(status=401)
        if self.check_hmac is True and hmac_check(
                await request.read(),
                self.cp_client._api_secret,
                request.headers.get("Content-HMAC")) is False:
            logger.warning(f"skip request from because hmac check failed: {request} from {request.remote}")
            return web.json_response(status=401)

        name = self._web_paths[request.url.name]
        notification_type = NOTIFICATION_TYPES.get(name)
        if notification_type is None:
            logger.error(f"notification type {name} not supported")
            return web.json_response(status=500)
        notification = notification_type(**(await request.post()))
        result = await self.process_notification(notification)
        if result == Result.INTERNAL_ERROR:
            return web.json_response(status=500)
        if result:
            return web.json_response({"code": result.value})


async def setup(app: web.Application):
    import config
    from . import handlers

    if None in [config.CP_PUBLIC_ID, config.CP_API_SECRET]:
        # Note: Was "Stopping.". If that's the case, replace the `return` with an exception
        logging.error("No Cloudpayments credentials found in env. Skipping initialization.")
        return

    methods.cp = AioCpClient(config.CP_PUBLIC_ID, config.CP_API_SECRET)

    # Now we are ready :)
    await methods.cp.test()

    dp: BusyCPDispatcher = BusyCPDispatcher()
    dp.include_router(handlers.cp_router)

    dp.setup(app, methods.cp)

    logging.info("Cloudpayments client and dispatcher initialized")


async def cleanup():
    if hasattr(globals(), "cp"):
        await methods.cp.disconnect()


__all__ = [
    "setup",
    "cleanup",
]
