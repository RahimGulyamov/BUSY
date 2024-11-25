from __future__ import annotations

import json
import typing

import datetime
import random
import string
import logging
import uuid
import functools


from aiohttp import web, web_request
import sqlalchemy
from sqlalchemy.ext.asyncio import AsyncSession

from ...common import responses, PHONE_FOR_APPSTORE, CODE_FOR_APPSTORE
from .. import command_dispatcher
from ... import db, voximplant, common
from ..cloudpayments import methods as cp_methods
from ..cloudpayments import types as cp_types

routes: web.RouteTableDef = web.RouteTableDef()


# Helper function
async def check_for_ban(phone: str) -> web.Response | None:
    curr_time: datetime.datetime = datetime.datetime.now()
    session: AsyncSession = db.DatabaseApi().cur_session

    # Check for ban
    ban: db.AuthBannedPhone | None = await common.is_phone_banned(phone)
    if ban is not None:
        return responses.too_many_requests(info=ban.reason,
                                           banTimeLeft=int((ban.end - curr_time).total_seconds()))

    # Ban if needed
    query: sqlalchemy.Select = sqlalchemy.select(db.AuthCode).\
        where((db.AuthCode.phone == phone) & (db.AuthCode.created_at + common.CODES_LIMIT_TIME > curr_time))

    if len((await session.scalars(query)).all()) >= common.CODES_LIMIT_AMOUNT:
        ban: db.AuthBannedPhone = common.ban_phone(phone, common.BAN_DURATION, "Too many code requests")
        return responses.too_many_requests(info=ban.reason,
                                           banTimeLeft=int(common.BAN_DURATION.total_seconds()))

    return None


def authorized(
    handler: typing.Callable[[web_request.Request], typing.Coroutine[None, None, web.Response]]
) -> typing.Callable[[web_request.Request], typing.Coroutine[None, None, web.Response]]:
    """
    A helper decorator to automatically check for a valid token.
    
    On validation failure, a matching response is issued.
    On validation success, the handler is called with keyword argument user_id: int = <user id>.
    
    Note: Place below @routes.get or @routes.post decorators!
    """
    
    @functools.wraps(handler)
    async def wrapper(request: web_request.Request) -> web.Response:
        token: str | None = request.query.get("token", None)
        
        if token is None:
            return responses.bad_request(info="missing required parameter: token")

        try:
            token: uuid.UUID = uuid.UUID(token)
        except ValueError:
            return responses.bad_request(info="invalid token")
        
        async with db.DatabaseApi().session() as session:
            auth_session: db.AuthSession | None = await session.get(db.AuthSession, token)
            
            if auth_session is None:
                return responses.bad_request(info="invalid token")
            
            # TODO: Check expiration!
            
            user_id: int = auth_session.user_id
        
        return await handler(request, user_id=user_id)
    
    return wrapper


@routes.get('/mobile/v1/auth_request')
async def auth_request(request: web_request.Request) -> web.Response:
    if "phone" not in request.url.query:
        return responses.bad_request(info="missing required parameter: phone")

    # TODO: checks?
    phone: str = request.url.query["phone"]

    async with db.DatabaseApi().session() as session:
        curr_time: datetime.datetime = datetime.datetime.now()

        ban_check_result : web.Response | None = await check_for_ban(phone)
        if ban_check_result is not None:
            return ban_check_result

        await common.ensure_for_auth_request(phone)

        if phone == PHONE_FOR_APPSTORE:
            code = CODE_FOR_APPSTORE
        else:
            code = ''.join(random.choice(string.digits) for _ in range(common.AUTH_CODE_LENGTH))

        new_code: db.AuthCode = db.AuthCode(
            phone=phone,
            code=code,
            created_at=datetime.datetime.now(),
            expires_at=datetime.datetime.now() + common.AUTH_CODE_VALID_TIME,
        )

        # Check for timeout
        latest_code: db.AuthCode | None = await common.get_latest_code(phone)
        if latest_code is not None and latest_code.created_at + common.AUTH_CODE_TRIES_TIMEOUT > curr_time:
            # Invalidate created code (add only to account for stats for ban)
            new_code.code = ""
            new_code.used = True
            session.add(new_code)

            latest_code_valid_time_left: int = (latest_code.expires_at - curr_time).total_seconds()
            return responses.too_many_requests(
                info="Too often code requests",
                nextTryTimeLeft=int(
                    (latest_code.created_at + common.AUTH_CODE_TRIES_TIMEOUT - curr_time)
                    .total_seconds()
                ),
                latestCodeValidTimeLeft=int(latest_code_valid_time_left),
            )
        # Finally
        session.add(new_code)
        
        from ...telegram.main import tell_mobile_auth_code as tg_tell_mobile_auth_code
        import config
        
        if config.BRANCH == "master":
            logging.info(f"Verification code for {phone} is {new_code.code}")
            await voximplant.client.send_sms_message(
                config.VOX_MAIN_NUMBER,
                phone,
                f"Ваш код подтверждения Busy: {new_code.code}",
            )
            
            user: db.User | None = await db.DatabaseApi().find_user(own_phone=phone)
            if user is not None and user.telegram_id is not None:
                await tg_tell_mobile_auth_code(user.telegram_id, new_code.code)
        
        return responses.success(
            result="ok",
            nextTryTimeout=int(common.AUTH_CODE_TRIES_TIMEOUT.total_seconds()),
            expireTimeout=int(common.AUTH_CODE_VALID_TIME.total_seconds()),
        )


@routes.get('/mobile/v1/auth')
async def auth(request: web_request.Request) -> web.Response:
    if "phone" not in request.url.query:
        return responses.bad_request(info="missing required parameter: phone")
    phone: str = request.url.query["phone"]

    if "code" not in request.url.query:
        return responses.bad_request(info="missing required parameter: code")
    code: str = request.url.query["code"]
    
    device_uuid: str
    if "device_uuid" in request.url.query:
        device_uuid = request.url.query["device_uuid"]
    else:
        logging.warning("Missing device_uuid in auth request. Ignoring for now")
        device_uuid = str(uuid.UUID(int=0))
    
    device_type: str
    if "device_type" in request.url.query:
        device_type = request.url.query["device_type"]
    else:
        logging.warning("Missing device_type in auth request. Ignoring for now")
        device_type = "android"

    # TODO: More checks?
    assert device_type in ("android", "ios")
    # Just as a check
    uuid.UUID(hex=device_uuid)

    async with db.DatabaseApi().session() as session:
        curr_time: datetime.datetime = datetime.datetime.now()

        ban_check_result: web.Response | None = await check_for_ban(phone)
        if ban_check_result is not None:
            return ban_check_result

        auth_request: db.AuthRequest = await common.ensure_for_auth_request(phone)
        code_obj: db.AuthCode | None = await common.check_code(phone, code)

        import config
        prod_mode = config.BRANCH == "master"

        if (prod_mode and code_obj is None) or (not prod_mode and code != "111111"):
            auth_request.fail_count += 1
            if auth_request.fail_count >= common.AUTH_CODE_FAILS_UNTIL_BAN:
                auth_request.status = "rejected"
                ban: db.AuthBannedPhone = common.ban_phone(phone, common.BAN_DURATION,
                                                           "Got too many incorrect auth codes")
                return responses.too_many_requests(info=ban.reason,
                                                   banTimeLeft=int(common.BAN_DURATION.total_seconds()))

            else:
                return responses.unauthorized(info="Incorrect auth code")

        # Code is correct
        if prod_mode:
            code_obj.used = True

        user: db.User = await common.ensure_for_user(phone)
        device: db.Device = await common.ensure_device(device_uuid, device_type)
        auth_session: db.AuthSession = db.AuthSession(token=uuid.uuid4(),
                                                      user=user,
                                                      device=device,
                                                      created_at=curr_time,
                                                      expires_at=curr_time + common.AUTH_SESSION_VALID_TIME)
        
        session.add(auth_session)
        return responses.success(
            result="ok",
            token=auth_session.token,
            # config=dict(await common.get_user_config(user)),
        )


@routes.get('/mobile/v1/logout')
@authorized
async def logout(request: web_request.Request, user_id: int) -> web.Response:
    token: str = request.url.query["token"]

    async with db.DatabaseApi().session() as session:
        # TODO: extract & move to db.interfaces?
        auth_session: db.AuthSession = await session.get(db.AuthSession, token)
        assert auth_session is not None
        
        auth_session.expired = True

        return responses.success(result="ok")


@routes.post('/mobile/v1/send_outgoing_sms')
@authorized
async def send_sms(request: web_request.Request, user_id: int) -> web.Response:
    if "dest_phone" not in request.url.query:
        return responses.bad_request(info="missing required parameter: dest_phone")

    if request.can_read_body is False:
        return responses.bad_request(info="can't read request body")

    try:
        body = await request.json()
    except json.JSONDecodeError:
        return responses.bad_request(info="body is not an valid JSON")

    if "msg" not in body:
        return responses.bad_request(info="missing required key in the body: msg")

    # TODO: checks?
    token: str = request.url.query["token"]
    dest_phone: str = request.url.query["dest_phone"]
    message: str = str(body["msg"])

    async with db.DatabaseApi().session() as session:
        user: db.User | None = await db.DatabaseApi().find_user(user_id=user_id)
        assert user is not None, "Authorized user is None somehow"

        if user.given_phone == "":
            return responses.bad_request(info="user doesn't have virtual number")

        if not (await common.bill(user, charge_msg=True)):
            return responses.bad_request(info="not paid")

        sms: db.SMS = db.SMS(user=user,
                             is_incoming=False,
                             from_phone=user.given_phone,
                             to_phone=dest_phone,
                             text=message,
                             timestamp=datetime.datetime.now(),
                             )

        session.add(sms)
        await voximplant.client.send_sms_message(user.given_phone, dest_phone, message)
        await common.handle_advance_service(user.id, charge_msg=True)
        return responses.success(result="ok")


@routes.get('/mobile/v1/calls')
@routes.get('/mobile/v1/calls/')  # Compatibility :)
@authorized
async def calls(request: web_request.Request, user_id: int) -> web.Response:
    # TODO: I hope exceptions are handled somwhere centalized
    
    # Note: negative limit means no limit
    limit: int = int(request.url.query.get("limit", 50))
    offset: int = int(request.url.query.get("offset", 0))
    assert offset >= 0
    
    calls_info: list[dict[str, typing.Any]] = []
    
    async with db.DatabaseApi().session() as session:
        query = sqlalchemy.select(db.Call)\
            .where(db.Call.user_id == user_id)\
            .order_by(db.Call.timestamp.desc())\
            .offset(offset)
        
        if limit > 0:
            query = query.limit(limit)
        
        calls = await session.execute(query)
        
        for call in calls.scalars():
            # TODO: Just ids instead?
            calls_info.append(common.call_info(call))
    
    return responses.success(result="ok", calls=calls_info)


@routes.get('/mobile/v1/calls/{call_id}')
@authorized
async def call(request: web_request.Request, user_id: int) -> web.Response:
    async with db.DatabaseApi().session() as session:
        call: db.Call | None = await session.get(db.Call, request.match_info["call_id"])
        
        if call is None:
            return responses.bad_request(info="call not found")
        
        if call.user_id != user_id:
            return responses.bad_request(info="you are not the owner of this call")

        return responses.success(result="ok", call=common.call_info(call, with_commands=True))


@routes.get('/mobile/v1/sms')
@routes.get('/mobile/v1/sms/')  # Compatibility :)
@authorized
async def sms(request: web_request.Request, user_id: int) -> web.Response:
    # Note: negative limit means no limit
    limit: int = int(request.url.query.get("limit", 50))
    offset: int = int(request.url.query.get("offset", 0))
    assert offset >= 0
    
    direction: str = request.url.query.get("direction", "all")
    assert direction in ("all", "incoming", "outgoing")
    direction_filter = lambda info: direction == "all" or info["direction"] == direction
    
    messages_info: list[dict[str, typing.Any]] = []
    
    async with db.DatabaseApi().session() as session:
        # TODO: Use date for ordering instead?
        query = sqlalchemy.select(db.SMS)\
            .where(db.SMS.user_id == user_id)\
            .order_by(db.SMS.id)\
            .offset(offset)
        
        if limit > 0:
            query = query.limit(limit)
        
        smss = await session.execute(query)
        
        for sms in smss.scalars():
            info = common.sms_info(sms)
            
            if not direction_filter(info):
                continue
            
            messages_info.append(info)
    
    return responses.success(result="ok", messages=messages_info)


@routes.get('/mobile/v1/incoming_ws')
@routes.get('/mobile/v1/incoming_ws/')  # Compatibility :)
@authorized
async def incoming_ws(request: web_request.Request, user_id: int) -> web.Response:
    async with db.DatabaseApi().session():
        call: db.Call | None = await common.find_active_call(user_id)
        if call is None:
            return responses.not_found(info="no active calls")

        logging.info(f"{call.uid} {command_dispatcher.finished_flag}")
        if call.uid not in command_dispatcher.finished_flag:
            return responses.not_found(info="call is not ready")

        call_id = call.uid

    ws = web.WebSocketResponse()
    await ws.prepare(request)
    logging.info(f'Client websocket connection starting on {request.url} for call {call_id}')

    # Make client to listen new commands
    command_dispatcher.client_websockets[call_id].append(ws)
    # Before main dispatching routine we must send existing commands history to the newly joined client
    # Iterate over copy as it might be changed (ws is already listening for new commands)
    for cmd in command_dispatcher.call_commands[call_id].copy():
        await ws.send_json(json.dumps(cmd))

    await command_dispatcher.handle_websocket(call_id, ws)
    command_dispatcher.client_websockets[call_id].remove(ws)

    if ws.closed is False:
        await ws.close()

    logging.info(f'Client websocket connection closed on {request.url} for call {call_id}')
    return web.Response(text='finish websocket handler')


@routes.get('/mobile/v1/active_plans')
@authorized
async def active_plans(request: web_request.Request, user_id: int) -> web.Response:
    plans_info: list[dict[str, typing.Any]] = []
    
    async with db.DatabaseApi().session() as session:
        user: db.User | None = await session.get(db.User, user_id)
        assert user is not None, "Authorized user is None somehow"

        active_plans: list[db.ActivePlan] = list(user.active_plans)
        
        # TODO: Not needed, I assume?
        # if user.subscription:
        #     active_plans.append(user.subscription)

        for active_plan in active_plans:
            plan: db.Plan = active_plan.plan
            
            # TODO: More info? Options?
            plans_info.append(dict(
                name=plan.name,
                is_extra=plan.is_extra,
                start_time=active_plan.start.timestamp(),
                end_time=active_plan.end.timestamp(),
                calls_left=active_plan.calls_left,
                messages_left=active_plan.messages_left,
                payment_id=active_plan.payment_id
            ))

    plans_info = sorted(plans_info, key= lambda d: d["start_time"], reverse=True)
    return responses.success(result="ok", plans=plans_info)


@routes.get('/mobile/v1/unsubscribe')
@authorized
async def unsubscribe(request: web_request.Request, user_id: int) -> web.Response:
    from ...telegram.main import user_unsubscribed_ext as user_unsubscribed_ext

    async with db.DatabaseApi().session() as session:
        user: db.User | None = await session.get(db.User, user_id)
        assert user is not None, "Authorized user is None somehow"

        await common.unsubscribe(user, reclaim_number=True)

        telegram_id = user.telegram_id

    if telegram_id is not None:
        await user_unsubscribed_ext(telegram_id)

    return responses.success(result="ok")


@routes.get('/mobile/v1/subscribe')
@authorized
async def subscribe(request: web_request.Request, user_id: int) -> web.Response:
    from ...telegram.main import successful_subscription as tg_successful_subscription

    if "plan_id" not in request.url.query:
        return responses.bad_request(info="missing required key in the body: plan_id")

    # TODO: checks?
    paymethod_force_change = "paymethod_force_change" in request.url.query
    plan_id: int = int(request.url.query["plan_id"])

    async with db.DatabaseApi().session():
        user: db.User = await db.DatabaseApi().find_user(user_id=user_id)
        assert user is not None, "Authorized user is None somehow"

        plan: db.Plan = await db.DatabaseApi().get_plan(plan_id=plan_id)
        if plan is None:
            return responses.bad_request(info="no such plan")

        if plan.is_extra:
            return responses.bad_request(info="subscription is allowed only for non-extra plans")

        if user.payment_token is None or paymethod_force_change:
            order = await cp_methods.create_order(user, plan, cp_types.PaymentReasons.REGULAR_PLAN_SUBSCRIPTION)
            return responses.success(result="ok", status="url", order_url=order.url, order_id=order.id)
        else:
            try:
                tx = await cp_methods.charge(user, plan, cp_types.PaymentReasons.REGULAR_PLAN_SUBSCRIPTION)
                virt_number: str = await common.change_subscription(user, plan, tx.transaction_id)

                if user.telegram_id is not None:
                    await tg_successful_subscription(user.telegram_id, plan_id, virt_number)

                return responses.success(result="ok",
                                         status="subscribed",
                                         payment_id=tx.transaction_id,
                                         virt_number=virt_number)

            except cp_types.CpPaymentError:
                logging.error("CpPaymentError:", exc_info=True)
                return responses.bad_request(info="payment_error")


@routes.get('/mobile/v1/activate_extra_plan')
@authorized
async def activate_extra_plan(request: web_request.Request, user_id: int) -> web.Response:
    from ...telegram.main import successful_payment as tg_successful_payment

    if "plan_id" not in request.url.query:
        return responses.bad_request(info="missing required key in the body: plan_id")

    # TODO: checks?
    paymethod_force_change = "paymethod_force_change" in request.url.query
    plan_id: int = int(request.url.query["plan_id"])

    async with db.DatabaseApi().session():
        user: db.User = await db.DatabaseApi().find_user(user_id=user_id)
        assert user is not None, "Authorized user is None somehow"

        plan: db.Plan = await db.DatabaseApi().get_plan(plan_id=plan_id)
        if plan is None:
            return responses.bad_request(info="no such plan")

        if not plan.is_extra:
            return responses.bad_request(info="allowed only for extra plans")

        if user.payment_token is None or paymethod_force_change:
            order = await cp_methods.create_order(user, plan, cp_types.PaymentReasons.EXTRA_PLAN)
            return responses.success(result="ok", status="url", order_url=order.url, order_id=order.id)
        else:
            try:
                tx = await cp_methods.charge(user, plan, cp_types.PaymentReasons.EXTRA_PLAN)
                await common.activate_extra_plan(user, plan, tx.transaction_id)

                if user.telegram_id is not None:
                    await tg_successful_payment(user.telegram_id, plan_id, plan.price)

                return responses.success(result="ok",
                                         status="activated",
                                         payment_id=tx.transaction_id)

            except cp_types.CpPaymentError:
                logging.error("CpPaymentError:", exc_info=True)
                return responses.bad_request(info="payment_error")


@routes.get('/mobile/v1/cancel_order')
@authorized
async def cancel_order(request: web_request.Request, user_id: int) -> web.Response:
    async with db.DatabaseApi().session():
        user: db.User = await db.DatabaseApi().find_user(user_id=user_id)
        assert user is not None, "Authorized user is None somehow"
        await cp_methods.cancel_order(user)

    return responses.success(result="ok")


@routes.get('/mobile/v1/user_info')
@authorized
async def user_info(request: web_request.Request, user_id: int) -> web.Response:
    async with db.DatabaseApi().session() as session:
        user: db.User = await db.DatabaseApi().find_user(user_id=user_id)
        assert user is not None, "Authorized user is None somehow"

        info = {
            "user_id": user.id,
            "subscription": user.subscription_id,
            "payment_method_string": user.payment_method_string,
            "extra_plan_autocharge": user.extra_plan_autocharge,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "own_phone": user.own_phone,
            "given_phone": user.given_phone
        }

        return responses.success(result="ok", **info)

__all__ = [
    "routes",
]
