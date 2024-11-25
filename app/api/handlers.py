from __future__ import annotations
import typing
import asyncio
import datetime
import logging
import uuid

from aiohttp import web, web_request, ClientSession

from urllib.parse import urlparse, parse_qs

from ..telegram.main import start_dialog as tg_start_dialog, finish as tg_finish, \
    unpaid_incoming_call_notification as tg_unbilled_incoming_call_notification

from .. import db, common
from ..common import responses
from .commands import *
from . import command_dispatcher

routes = web.RouteTableDef()

response_not_paid: web.Response = responses.success(result='reject', info='notPaid')
response_wrong_key: web.Response = responses.success(result='reject', info='wrongKey')
response_wrong_data: web.Response = responses.bad_request(info='wrongData')


def is_correct_api_key(url: str) -> bool:
    import config
    api_key = config.API_KEY

    parsed_url = urlparse(url)
    url_api_key = parse_qs(parsed_url.query)['apiKey'][0]
    return api_key == url_api_key


@routes.post('/voximplant/v1/calls/connection')
async def call_from_voximplant(request: web_request.Request) -> web.Response:
    request_text = await request.text()
    logging.info(f'got request with {request_text=}')

    if is_correct_api_key(str(request.url)) is False:
        logging.info(f"Wrong api key on {request.url}")
        return response_wrong_key

    body = await request.json()
    user_number: str | None = body['userNumber']
    caller_number: str | None = body['callerNumber']
    if user_number is None or caller_number is None:
        return response_wrong_data
    
    user_number = common.strip_number(user_number)
    caller_number = common.strip_number(caller_number)

    async with db.DatabaseApi().session() as session:
        user = await db.DatabaseApi().find_user(own_phone=user_number)
        if user is None:
            logging.warning(f'User with number {user_number} is not found')
            return responses.not_found(info="user not found")

        # if await db.DatabaseApi().has_current_call(user_id=user.id):
        #     logging.info("User already has incoming call")
        #     return responses.has_current_call()

        if not (await common.bill(user, charge_call=True)):
            await tg_unbilled_incoming_call_notification(user.telegram_id, caller_number)
            return response_not_paid

        if common.normalize_phone(caller_number) in (await common.get_user_config(user))['IGNORE_LIST']:
            logging.info("Call from ignored number")
            return responses.is_ignored()

        call = db.model.Call(
            uid=uuid.uuid4(),
            user_id=user.id,
            callee_number=user_number,
            caller_number=caller_number,
            timestamp=datetime.datetime.now(),
        )
        session.add(call)

        await common.send_push_to_user(f"Входящий звонок от {caller_number}", user)

        # Create it here as Telegram bot is allowed to put messages to it right on the following line :)
        command_dispatcher.tg_call_commands_queues[call.uid] = asyncio.Queue()

        await tg_start_dialog(
            telegram_id=user.telegram_id,
            number=caller_number,
            call_id=str(call.uid),
        )

        user_id = user.id
        devices = await db.DatabaseApi().get_devices(user_id=user_id)
        if devices is None:
            sdk = 'false'
        else:
            sdk = 'true'

        return responses.success(
            callId=call.uid,
            sdk=sdk,
            config=dict(await common.get_user_config(user)),
        )


@routes.get('/')
async def hello(request: web_request.Request) -> web.Response:
    return web.Response(text="Hello, world")


@routes.get('/voximplant/v1/calls/{callId}/websocket')
async def websocket_handler(request: web_request.Request) -> web.Response:
    if is_correct_api_key(str(request.url)) is False:
        logging.info(f"Wrong api key on {request.url}")
        return response_wrong_key

    call_id = uuid.UUID(request.match_info['callId'])

    # NOTE: Here is the entry point for the whole incoming call process !!!

    ws = web.WebSocketResponse()
    await ws.prepare(request)
    logging.info(f'Vox websocket connection starting on {request.url}')

    command_dispatcher.client_websockets[call_id] = []
    command_dispatcher.call_commands[call_id] = []
    # Mark that we ready to handle client websockets
    command_dispatcher.finished_flag[call_id] = False

    # This task is created here because the call is initiated by Voximplant
    process_tg_queue_task = asyncio.create_task(command_dispatcher.process_tg_queue(call_id))

    command_dispatcher.client_websockets[call_id].append(ws)
    await command_dispatcher.handle_websocket(call_id, ws, is_vox=True)
    command_dispatcher.client_websockets[call_id].remove(ws)

    process_tg_queue_task.cancel()

    if ws.closed is False:
        await ws.close()

    logging.info(f'Vox websocket connection closed on {request.url}')
    return web.Response(text='finish websocket handler')


@routes.post('/voximplant/v1/calls/{callId}/connection/outbound')
async def start_outbound_call(request: web_request.Request) -> web.Response:
    if is_correct_api_key(str(request.url)) is False:
        logging.info(f"Wrong api key on {request.url}")
        return response_wrong_key

    body = await request.json()
    timestamp: str | None = body.get('timestamp')
    destination: str | None = body.get('destinationNumber')
    caller_number: str | None = body.get('callerNumber')
    if timestamp is None or destination is None or caller_number is None:
        return response_wrong_data
    
    destination = common.strip_number(destination)
    caller_number = common.strip_number(caller_number)

    call_id = uuid.UUID(request.match_info['callId'])

    async with db.DatabaseApi().session() as session:
        user: db.User = await db.DatabaseApi().find_user(own_phone=caller_number)

        call_object = await db.DatabaseApi().get_call_object(call_id=call_id)
        if call_object is None:
            call = db.model.Call(
                uid=call_id,
                user_id=user.id,
                callee_number=destination,
                caller_number=caller_number,
                timestamp=datetime.datetime.fromtimestamp(float(timestamp)),
            )
            session.add(call)
        else:
            call_object.timestamp = datetime.datetime.fromtimestamp(float(timestamp))

    return responses.success(call_id=call_id)


@routes.post('/voximplant/v1/calls/{callId}/outbound/data')
async def get_outbound_data(request: web.Request) -> web.Response:
    request_text = await request.text()
    logging.info(f'got request with {request_text=}')

    if is_correct_api_key(str(request.url)) is False:
        logging.info(f"Wrong api key on {request.url}")
        return response_wrong_key

    body = await request.json()
    session_id: str | None = body.get('sessionId')
    record: str | None = body.get('record')
    if (session_id or record) is None:
        return response_wrong_data

    call_id = uuid.UUID(request.match_info['callId'])
    async with db.DatabaseApi().session():
        call = await db.DatabaseApi().get_call_object(call_id=call_id)
        call.recording_url = record
        call.finished = True

        await common.handle_advance_service(call.user.id, charge_call=True)

    return responses.success(call_id=call_id)


@routes.post('/voximplant/v1/calls/{callId}/data')
async def get_call_data(request: web_request.Request) -> web.Response:
    request_text = await request.text()
    logging.info(f'got request with {request_text=}')

    if is_correct_api_key(str(request.url)) is False:
        logging.info(f"Wrong api key on {request.url}")
        return response_wrong_key

    body = await request.json()
    commands_json_list: list | None = body['commands']
    record: str | None = body['record']

    if (commands_json_list or record) is None:
        return response_wrong_data

    commands = command_dispatcher.refine_call_history([json.loads(command) for command in commands_json_list])
    call_id = uuid.UUID(request.match_info['callId'])

    if call_id is None:
        return response_wrong_data

    async with db.DatabaseApi().session():
        call_object = await db.DatabaseApi().get_call_object(call_id=call_id)
        call_object.recording_url = record
        call_object.extra_data = commands
        user = await db.DatabaseApi().find_user(user_id=call_object.user_id)
        user_id = user.id
        telegram_id = user.telegram_id

    # Wait processing all commands until 'finish' command
    time_waited: int = 0
    timeout: int = 300
    while not command_dispatcher.finished_flag[call_id]:
        await asyncio.sleep(1)
        time_waited += 1
        if time_waited > timeout:
            break

    await tg_finish(telegram_id, commands, record)
    await common.handle_advance_service(user_id, charge_call=True)
    return responses.success(callId=call_id)


outbound_calls_sessions = []


@routes.post('/voximplant/v1/sms')
async def management_api_webhook(request: web_request.Request) -> web.Response:
    import config

    if is_correct_api_key(str(request.url)) is False:
        logging.info(f"Wrong api key on {request.url}")
        return response_wrong_key

    body = await request.json()
    logging.info(f"Got Voximplant Management API callbacks: {body}")

    # Should be fixed after appearing new tariffs on production
    # if config.BRANCH == 'master':
    #     async with ClientSession() as session:
    #         async with session.post(url=f'https://test.busy.contact/voximplant/v1/sms?apiKey={config.API_KEY}',
    #                                 data=json.dumps(body)) as response:
    #             if response.status != 200:
    #                 logging.warning("Unsuccessful post-request to test host")

    # elif config.BRANCH == 'test':

    try:
        for callback in body['callbacks']:
            callback_type = callback.get('type')
            if callback_type == 'sms_inbound':
                await common.process_incoming_sms(callback=callback)
            elif callback_type == 'transcription_complete':
                try:
                    transcript_data = callback.get('transcription_complete')
                    if transcript_data is None:
                        logging.warning('Transcription_complete is None')
                        return responses.bad_request()

                    session_id = str(transcript_data.get('call_session_history_id'))
                    if session_id is None:
                        logging.warning('Session Id is None')
                        return responses.bad_request()
                except Exception as e:
                    logging.error(e)
                    return responses.bad_request()

                if session_id in outbound_calls_sessions:
                    logging.info("Duplicated webhook")
                    return responses.success()
                else:
                    outbound_calls_sessions.append(session_id)
                    logging.info(f"{session_id} was appended")
                    await common.get_transcript_commands(callback=callback)
                    outbound_calls_sessions.remove(session_id)
                    logging.info(f"{session_id} was removed")
    except Exception as error:
        logging.warning(f"Maybe you are trying to get sms or outbound call to test bot\n{error}")

    return responses.success()
