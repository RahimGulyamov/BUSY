import json
from time import time
from uuid import uuid4


async def get_command_message(message_text=f'Время отправки сообщения {time()}') -> json:
    return json.dumps(dict(
        command='message',
        id=str(uuid4()),
        timestamp=str(time()),
        side='user',
        text=message_text,
        type='whole'
    ))


async def get_command_connect() -> json:
    return json.dumps(dict(
        command='connect',
        id=str(uuid4()),
        timestamp=str(time()),
        side='user',
        destinationNumber='9377827811'
    ))


async def get_command_finish() -> json:
    return json.dumps(dict(
        command='finish',
        id=str(uuid4()),
        timestamp=str(time()),
        side='user',
        status='ok'
    ))


async def get_command_answer() -> json:
    return json.dumps(dict(
        command='answer',
        id=str(uuid4()),
        timestamp=str(time()),
    ))


async def get_command_busy() -> json:
    return json.dumps(dict(
        command='busy',
        id=str(uuid4()),
        timestamp=str(time()),
        side='user'
    ))


async def get_command_recall() -> json:
    return json.dumps(dict(
        command='recall',
        id=str(uuid4()),
        timestamp=str(time()),
        side='user'
    ))
