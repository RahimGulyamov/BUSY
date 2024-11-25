import asyncio
from .oauth_client import AmoOAuthClient
from datetime import datetime
import logging

from .entities import *
from .oauth_client import AmoOAuthClient
from ..db.interface import DatabaseApi

client: AmoOAuthClient


async def run() -> None:
    import config

    async with DatabaseApi().session():
        access_token, refresh_token = await DatabaseApi().get_amo_tokens()

    crm_url = config.AMO_CRM_URL
    client_id = config.AMO_CRM_CLIENT_ID
    client_secret = config.AMO_CRM_CLIENT_SECRET
    redirect_url = 'https://test.busy.contact'

    if (access_token is None) or (refresh_token is None) or (client_secret is None):
        logging.error("No right data for amoCRM client")
        return

    global client
    client = AmoOAuthClient(access_token, refresh_token, crm_url, client_id, client_secret, redirect_url)

    # Nothing left to do, exiting early


__all__ = [
    "run",
    "client",
    "entities",
]
