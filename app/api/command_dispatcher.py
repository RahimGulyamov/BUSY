import asyncio
import datetime
import json
import logging
import typing
import uuid

from aiohttp import web, WSMsgType, WSMessage

from .. import db, common
from ..telegram.main import process_command as tg_process_command
from .command_queues import *


async def handle_websocket(call_id: uuid.UUID, ws: web.WebSocketResponse, is_vox: bool = False):
    logging.info(f"Started handling websocket for call {call_id}")
    async with db.DatabaseApi().session(allow_reuse=True):
        call: db.Call | None = await db.DatabaseApi().get_call_object(call_id=call_id)
        user: db.User | None = call.user
        assert call is not None, "Call is gone somewhere while processing"

        async for message in ws:
            if finished_flag[call_id]:
                break

            if isinstance(message, WSMessage) and message.type == WSMsgType.text:
                command_json = message.json()
                await handle_new_command(call_id, user.telegram_id, command_json, src_ws=ws, got_from_vox=is_vox)
    logging.info(f"Stopped handling websocket for call {call_id}")


# NOTE: MUST BE CALLED ONLY UNDER EXISTING DB SESSION!!!
async def handle_new_command(call_id: uuid.UUID,
                             telegram_id: str | None,  # Treated only if not got_from_tg
                             cmd: typing.Any,
                             *,
                             got_from_tg: bool = False,
                             got_from_vox: bool = False,
                             src_ws: web.WebSocketResponse | None = None) -> None:
    assert got_from_tg or src_ws is not None, "Unknown command source"
    assert not (got_from_tg and got_from_vox), "Multiple command source"

    async with db.DatabaseApi().session():
        insert: bool = False
        if got_from_vox:
            # Voximplant can emit commands with existing id for partial replicas and reassigned timestamps
            # Note than old command versions still remains in call_commands list
            command_from_db = await db.DatabaseApi().get_command(command_id=cmd['id'])
            if command_from_db is not None:
                command_from_db.timestamp = datetime.datetime.fromtimestamp(float(cmd['timestamp']))
                command_from_db.contents = common.form_command_contents(cmd)
            else:
                insert = True
        else:
            insert = True

        if insert:
            command_object = common.form_command_to_db(call_id, cmd)
            await db.DatabaseApi().put_command(command_object)

    call_commands[call_id].append(cmd)

    if not got_from_tg and telegram_id is not None:
        await tg_process_command(telegram_id, get_refined_call_history(call_id))

    # Iterate over copy as it might be changed
    for ws in client_websockets[call_id].copy():
        if ws is not src_ws:
            await ws.send_json(json.dumps(cmd))

    if cmd['command'] == 'finish' and got_from_vox:
        async with db.DatabaseApi().session():
            call: db.Call | None = await db.DatabaseApi().get_call_object(call_id=call_id)
            finished_flag[call_id] = True
            call.finished = True
            
            user: db.User = call.user
            
            await common.send_push_to_user("Звонок завершён", user)


async def process_tg_queue(call_id: uuid.UUID) -> None:
    try:
        call_queue = tg_call_commands_queues[call_id]
        while not finished_flag[call_id]:
            command_json = await call_queue.get()
            if command_json is not None:
                cmd = json.loads(command_json)
                await handle_new_command(call_id, None, cmd, got_from_tg=True)
    except asyncio.CancelledError:
        #logging.error(f"Got asyncio.CancelledError for tg processing for call {call_id}")
        pass
    except:
        logging.error(f"Exception occured while processing tg queue for call {call_id}:", exc_info=True)


# Picks the latest version among commands with the same call id
def refine_call_history(commands: typing.List[typing.Any]) -> typing.List[typing.Any]:
    cmds_by_id = {}
    for cmd in commands:
        cmd_id = cmd['id']
        if cmd_id in cmds_by_id:
            ex_cmd = cmds_by_id[cmd_id]
            if float(cmd['timestamp']) > float(ex_cmd['timestamp']):
                cmds_by_id[cmd_id] = cmd
        else:
            cmds_by_id[cmd_id] = cmd

    return sorted(cmds_by_id.values(), key=lambda cmd: float(cmd['timestamp']))


def get_refined_call_history(call_id: uuid.UUID) -> typing.List[typing.Any]:
    return refine_call_history(call_commands[call_id])