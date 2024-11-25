import asyncio
import typing
import uuid

from aiohttp import web

# Separated from commands dispatcher due to cyclic import errors =(

# Command queues for each call for getting commands from Telegram bot
# Behaving like a websocket, provides a uniform interface for receiving commands from all clients
tg_call_commands_queues: typing.Dict[uuid.UUID, asyncio.Queue] = {}

# List of clients websockets for each call
client_websockets: typing.Dict[uuid.UUID, typing.List[web.WebSocketResponse]] = {}

# Complete history of commands for each call
call_commands: typing.Dict[uuid.UUID, typing.List[typing.Any]] = {}

# Store for each call its finish flag
# Absense of thw call in this dict means that call is not ready (Voximplant haven't opened its websocket yet)
finished_flag: typing.Dict[uuid.UUID, bool] = {}