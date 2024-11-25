from __future__ import annotations

import dataclasses
import datetime
import json
import logging
import typing
import uuid
import asyncio
from types import MappingProxyType
import re
import enum

import sqlalchemy
from sqlalchemy.ext.asyncio import AsyncSession
import aiogram.utils.exceptions as aiogram_exceptions

from .. import db
from ..api.mobile import onesignal
from ..api.cloudpayments import methods as cp_methods
from .. import voximplant
from ..common import billing_actions, assistant_config
from ..scheduler import *
from ..cloud_storage.interface import CloudStorageAPI
from ..common.extra_data_utils import UserFreeTrialUtil
from  ..telegram import texts

from ..api.cloudpayments import methods as cp_methods
from ..api.cloudpayments import types as cp_types
from aiocloudpayments.exceptions import CpPaymentError

# WARNING: making it non-midnight can break things which are done before this moment of a day :)
BILLING_TIME: datetime.time = datetime.time(hour=0)
BILLING_PERIOD: datetime.timedelta = datetime.timedelta(days=30)
AUTO_KICK_PERIOD: datetime.timedelta = datetime.timedelta(days=30)
VIRT_NUMBER_RECLAIM_PERIOD: datetime.timedelta = datetime.timedelta(days=30)
CHARGE_RETRY_PERIOD: datetime.timedelta = datetime.timedelta(days=1)
CHARGE_AHEAD_TIME: datetime.timedelta = datetime.timedelta(days=1)
CHARGE_RETRIES_COUNT: int = 2  # Actually +1
FREE_TRIAL_PERIOD: datetime.timedelta = datetime.timedelta(days=14)
# For debug purposes
# BILLING_TIME: datetime.time = datetime.time(hour=0)
# BILLING_PERIOD: datetime.timedelta = datetime.timedelta(minutes=4)
# AUTO_KICK_PERIOD: datetime.timedelta = datetime.timedelta(seconds=30)
# VIRT_NUMBER_RECLAIM_PERIOD: datetime.timedelta = datetime.timedelta(minutes=4)
# CHARGE_RETRY_PERIOD: datetime.timedelta = datetime.timedelta(seconds=15)
# CHARGE_AHEAD_TIME: datetime.timedelta = datetime.timedelta(seconds=15)
# CHARGE_RETRIES_COUNT: int = 2  # Actually +1

INF_PLAN_LIMIT: int = 2400

AUTH_CODE_LENGTH: int = 6
AUTH_CODE_TRIES_TIMEOUT: datetime.timedelta = datetime.timedelta(minutes=1)

AUTH_REQUEST_VALID_TIME: datetime.timedelta = datetime.timedelta(hours=2)  # ???
AUTH_CODE_VALID_TIME: datetime.timedelta = datetime.timedelta(minutes=10)
AUTH_SESSION_VALID_TIME: datetime.timedelta = datetime.timedelta(days=7)  # ???

BAN_DURATION: datetime.timedelta = datetime.timedelta(minutes=10)
AUTH_CODE_FAILS_UNTIL_BAN: int = 10

CODES_LIMIT_TIME: datetime.timedelta = datetime.timedelta(minutes=15)
CODES_LIMIT_AMOUNT: int = 10

PHONE_FOR_APPSTORE = '79859455373'
CODE_FOR_APPSTORE = '123321'

class Plans(enum.IntEnum):
    """
    These are not arbitrary numbers, but actual database IDs!
    
    Note: the hardcoded values here are 
    """

    VERY_BUSY = 1
    SUPER_BUSY = 2
    ULTRA_BUSY = 3
    EXTRA = 4
    VERY_BUSY_TEAM = 5
    SUPER_BUSY_TEAM = 6
    
    @staticmethod
    def get_name(plan: Plans | int) -> str:
        return _plan_names.get(plan, "Unknown")


_plan_names: dict[Plans, str] = {
    Plans.VERY_BUSY: "Very Busy",
    Plans.SUPER_BUSY: "Super Busy",
    Plans.ULTRA_BUSY: "Ultra Busy",
    Plans.EXTRA: "Extra",
    Plans.VERY_BUSY_TEAM: "Very Busy Team",
    Plans.SUPER_BUSY_TEAM: "Super Busy Team",
}


def _define_plans() -> typing.Iterable[db.Plan]:
    """
    Lists the plans to be stored in the database.
    
    Do not use in your code, access the database instead!
    """
    
    yield db.Plan(
        id=Plans.VERY_BUSY, name=_plan_names[Plans.VERY_BUSY], price=299, months=1, is_extra=False,
        calls=50, messages=0, extra_data={}, 
    )
    
    yield db.Plan(
        id=Plans.SUPER_BUSY, name=_plan_names[Plans.SUPER_BUSY], price=599, months=1, is_extra=False,
        calls=50, messages=150, extra_data={}, 
    )
    
    yield db.Plan(
        id=Plans.ULTRA_BUSY, name=_plan_names[Plans.ULTRA_BUSY], price=2990, months=6, is_extra=False,
        calls=50 * 6, messages=150 * 6, extra_data={}, 
    )
    
    yield db.Plan(
        id=Plans.EXTRA, name=_plan_names[Plans.EXTRA], price=99, months=1, is_extra=True,
        calls=20, messages=50, extra_data={}, 
    )
    
    yield db.Plan(
        id=Plans.VERY_BUSY_TEAM, name=_plan_names[Plans.VERY_BUSY_TEAM], price=1, months=1, is_extra=False,
        calls=500, messages=0, extra_data={}, 
    )
    
    yield db.Plan(
        id=Plans.SUPER_BUSY_TEAM, name=_plan_names[Plans.SUPER_BUSY_TEAM], price=1, months=1, is_extra=False,
        calls=500, messages=500, extra_data={}, 
    )


async def create_plans() -> None:
    """
    Note: doesn't remove any unwanted plans from the database to avoid foreign key issues.
    """
    
    session: AsyncSession = db.DatabaseApi().cur_session
    
    for plan in _define_plans():
        # Note: just deleting and adding is not viable, because it would
        #       temporarily violate foreign key constraints.
        #       This means we have to do this as a manual "upsert".
        old_plan: db.Plan | None = await db.DatabaseApi().get_plan(plan_id=plan.id)
        if old_plan:
            for key in sqlalchemy.inspect(db.Plan).column_attrs.keys():
                setattr(old_plan, key, getattr(plan, key))
        else:
            session.add(plan)
    
    await session.flush()


async def validate_plans() -> bool:
    session: AsyncSession = db.DatabaseApi().cur_session
    
    success: bool = True
    
    for expected_plan in _define_plans():
        plan: db.Plan | None = await session.get(db.Plan, expected_plan.id)
        
        if plan is None:
            logging.warning("Expected plan missing from db", extra=dict(
                plan_id=expected_plan.id,
            ))
            success = False
            continue
        
        for key in sqlalchemy.inspect(db.Plan).column_attrs.keys():
            if getattr(plan, key) != getattr(expected_plan, key):
                logging.warning("Plan mismatch", extra=dict(
                    plan_id=expected_plan.id,
                    key=key,
                    value=getattr(plan, key),
                    expected_value=getattr(expected_plan, key),
                ))
                success = False
    
    return success


class Options(enum.IntEnum):
    """
    These are not arbitrary numbers, but actual database IDs!
    """

    VIRTUAL_NUMBER = 2


class AdvanceServiceState(enum.IntEnum):
    UNUSED = 1
    IN_PROGRESS = 2
    NOTIFIED = 3


class UnpaidStatus(enum.IntEnum):
    NO_SUBSCRIPTION = 1
    SUBSCRIPTION_UNPAID = 2
    OUT_OF_PLAN_EXTRA_UNPAID = 3
    OUT_OF_PLAN_NO_EXTRA_AUTOCHARGE = 4


class ExtraData:
    # Special flags that makes "retry" buttons in messages about failed payments inactive
    # if failed payment is no longer present
    FAILED_RECURRENT_RECOVERED = "failed_recurrent_recovered"
    FAILED_EXTRA_RECOVERED = "failed_extra_recovered"

    # State of provision of the service in advance (single unpaid service is allowed)
    ADVANCED_SERVICE_STATE = "advance_service_state"


async def add_user(user: db.User) -> None:
    session: AsyncSession = db.DatabaseApi().cur_session
    session.add(user)


async def subscribe(
    user: db.User,
    plan: db.Plan,
    payment_id: int | None = None,
    free_trial: bool = False,
) -> str:
    """
    :return: virtual number
    """

    # Just check that we are in existing DB session
    db.DatabaseApi().cur_session

    assert user.subscription is None, "User already has some subscription somehow..."

    if free_trial:
        util = UserFreeTrialUtil(user)
        assert util.can_use(), "Unauthorised free_trial!"
        util.mark_used()
        del util

    user.subscription = plan
    next_active_plan_start = await activate_plan(
        user, plan, payment_id,
        override_duration=FREE_TRIAL_PERIOD if free_trial else None,
    )
    await billing_actions.RecurrentPaymentAction(
        user.id,
        retries_left=CHARGE_RETRIES_COUNT,
    ).schedule(next_active_plan_start - CHARGE_AHEAD_TIME)

    if await plan_has_virtual_number(plan) and user.given_phone == "" and payment_id is not None:
        return await assign_virtual_number(user)
    else:
        # Return existing virtual number if present
        return user.given_phone


async def activate_plan(
    user: db.User,
    plan: db.Plan,
    payment_id: int | None = None,
    *,
    override_start_date: datetime.date | None = None,
    override_end_date: datetime.date | None = None,
    override_duration: datetime.timedelta | None = None,
) -> datetime.datetime:
    """
    Makes a new active plan from given plan.
    
    If `override_start_date` is not set, new active plan starts right after non-extra active plan
    actual at the time, or starts right now if actual active plans are not present at the time.
    
    If `override_end_date`/`override_duration` is not set, new active plan ends in `BILLING_PERIOD`
    after start.
    
    Note: this doesn't perform any payment-related actions
    
    :return: end date of the new active plan
    """
    
    session: AsyncSession = db.DatabaseApi().cur_session
    
    logging.debug(f"Activating plan {plan.id} ({plan.name}) for user {user.id} ({user.get_pretty_name()})")
    
    # Just check that we are in existing DB session
    db.DatabaseApi().cur_session
    
    assert override_end_date is None or override_duration is None, \
        "Override duration and end date are mutually exclusive"

    # Note: removed this restriction for free plans
    # assert override_end_date is None or plan.is_extra, \
    #     "Overriding end date must be used only for extra plans"

    months: int = plan.months if plan.months is not None else INF_PLAN_LIMIT

    billing_period_start: datetime.datetime = datetime.datetime.combine(
        override_start_date or datetime.date.today(),
        BILLING_TIME,
    )

    # billing_period_start = datetime.datetime.now()

    # In order to support billing one day before period starts
    curr_active_plan: db.ActivePlan | None = await get_main_active_plan(user)
    if curr_active_plan is not None and override_start_date is None:
        billing_period_start = curr_active_plan.end

    # Note: Used to create `months` active plans, now just a single big one.
    # This didn't affect billing, but it meant that the database held plan resources per month.
    # With this change, it becomes per plan duration instead, so the database entry
    # for Ultra Busy needs to be updated
    billing_period_end: datetime.datetime = billing_period_start + BILLING_PERIOD * months
    if override_end_date is not None:
        billing_period_end = datetime.datetime.combine(override_end_date, BILLING_TIME)
    elif override_duration is not None:
        assert override_duration.seconds == 0 and override_duration.microseconds == 0, \
            "override_duration must be an exact number of days"
        billing_period_end = billing_period_start + override_duration

    new_active_plan: db.ActivePlan = db.ActivePlan(
        user=user,
        plan=plan,
        calls_left=plan.calls,
        messages_left=plan.messages,
        start=billing_period_start,
        end=billing_period_end,
        payment_id=payment_id,
    )
    session.add(new_active_plan)

    return billing_period_end


async def renew_subscription(user: db.User, payment_id: int | None = None):
    plan: db.Plan = user.subscription
    next_active_plan_start = await activate_plan(user, plan, payment_id)
    await billing_actions.cancel_billing_punishment(user.id)
    # TODO: subtract CHARGE_RETRIES_COUNT?
    await billing_actions.RecurrentPaymentAction(
        user.id,
        retries_left=CHARGE_RETRIES_COUNT,
    ).schedule(next_active_plan_start)


async def activate_extra_plan(user: db.User, extra_plan: db.Plan, payment_id: int | None = None):
    active_plan: db.ActivePlan | None = await get_active_plan(user)
    assert active_plan is not None, "The main plan should be active to activate extra plans"
    await activate_plan(user, extra_plan,
                        payment_id=payment_id,
                        override_start_date=datetime.date.today(),
                        override_end_date=active_plan.end.date())

    await billing_actions.cancel_extra_punishments(user.id)
    user.extra_data = user.extra_data | {ExtraData.ADVANCED_SERVICE_STATE: AdvanceServiceState.UNUSED}


async def unsubscribe(user: db.User, *, reclaim_number: bool = False, cancel_actions: bool = True) -> None:
    session: AsyncSession = db.DatabaseApi().cur_session
    
    logging.debug(f"Unsubscribing user {user.id} ({user.get_pretty_name()})")
    
    user.subscription = None

    if cancel_actions:
        await billing_actions.cancel_billing_actions(user.id)

    if reclaim_number and user.given_phone != "":
        # TODO: put phone to some pool idk
        user.given_phone = ""

    # Delete all active plans
    await session.execute(sqlalchemy.delete(db.ActivePlan).where(db.ActivePlan.user == user))


async def change_subscription(
    user: db.User,
    plan: db.Plan,
    payment_id: int | None = None,
    free_trial: bool = False,
) -> str:
    if user.subscription is not None:
        # Reclaim number if switching to Very Busy
        await unsubscribe(user, reclaim_number=not await plan_has_virtual_number(plan))

    return await subscribe(user, plan, payment_id, free_trial=free_trial)


async def get_active_plans(user: db.User) -> typing.List[db.ActivePlan]:
    session: AsyncSession = db.DatabaseApi().cur_session
    now = datetime.datetime.now()
    query: sqlalchemy.Select = sqlalchemy.select(db.ActivePlan) \
        .where(sqlalchemy.and_(db.ActivePlan.user == user, db.ActivePlan.start <= now, now <= db.ActivePlan.end))

    return (await session.scalars(query)).all()


async def get_main_active_plan(user: db.User) -> db.ActivePlan | None:
    # Just check that we are in existing DB session
    db.DatabaseApi().cur_session

    for active_plan in await get_active_plans(user):
        if not active_plan.plan.is_extra:
            return active_plan
    
    return None


async def plan_has_virtual_number(plan: db.Plan) -> bool:
    # Just check that we are in existing DB session
    db.DatabaseApi().cur_session

    option: db.Option = await db.DatabaseApi().get_option(option_id=Options.VIRTUAL_NUMBER)
    return option in plan.options


# Returns active plan that have calls or messages
async def get_active_plan(user: db.User, *,
                          need_calls: bool = False,
                          need_messages: bool = False) -> db.ActivePlan | None:
    # Just check that we are in existing DB session
    db.DatabaseApi().cur_session

    active_plans = await get_active_plans(user)
    for active_plan in active_plans:
        if need_calls and active_plan.calls_left == 0:
            continue

        if need_messages and active_plan.messages_left == 0:
            continue

        return active_plan

    return None


# WARNING: Invocation of this function costs us a lot of money! Use it with caution!
async def assign_virtual_number(user: db.User) -> str | None:
    """
    return: new phone number
    """

    session: AsyncSession = db.DatabaseApi().cur_session
    
    logging.debug(f"Assigning virtual number to user {user.id} ({user.get_pretty_name()})")
    
    import config

    if config.BRANCH == 'master':
        buy_number_data = await voximplant.client.buy_new_number()

        if buy_number_data is not None:
            new_number = buy_number_data[0]
            installation_price = buy_number_data[1]
            monthly_price = buy_number_data[2]
            logging.info(f'Bought number {new_number} with installation price = {installation_price} \
                         and monthly price = {monthly_price}')

            user.given_phone = new_number
            # await session.commit()
            return new_number
        else:
            logging.warning('Error while buying number')
            return None
    elif config.BRANCH == 'test':
        # Mocking
        user.given_phone = config.VOX_MAIN_NUMBER
        return config.VOX_MAIN_NUMBER
    else:
        return ''


# I'll keep this here to be used later for storing text message call commands
async def send_call_messagee(user: db.User, text: str) -> None:
    raise NotImplementedError("Not expected to be used yet... Remove this if you know what you're doing.")

    session: AsyncSession = db.DatabaseApi().cur_session

    # TODO: Use .finished instead of .recording_url
    call: db.Call | None = await session.scalar(
        sqlalchemy.select(db.Call).where((db.Call.user == user) & (db.Call.recording_url == None))
    )

    if not call:
        logging.warning(f"User {user.id} has no active call. Ignoring command.")
        return

    cmd_uid: uuid.UUID = uuid.uuid4()

    call.commands.add(db.Command(
        uid=cmd_uid,
        timestamp=datetime.datetime.now(),
        comand="message",
        contents=dict(
            side="user",
            text=text,
            type="whole",
        ),
    ))


def form_command_contents(data: json):
    command = data['command']
    contents = {}

    if command == 'message':
        contents = dict(side=data.get('side'),
                        text=data.get('text'),
                        type=data.get('type'),
                        url=data.get('url', None))
    elif command == 'connect':
        contents = dict(side=data.get('side'),
                        destination_number=data.get('destinationNumber'))
    elif command == 'finish':
        contents = dict(side=data.get('side'),
                        status=data.get('status'),
                        code=data.get('code', None))
    elif command == 'busy':
        contents = dict(side=data.get('side'))

    return contents


def form_command_to_db(call_id: uuid.UUID, data: json) -> db.model.Command:
    data_id = data['id']
    command = data['command']
    timestamp = datetime.datetime.fromtimestamp(float(data['timestamp']))

    model_message = db.model.Command(
        uid=data_id,
        call_uid=call_id,
        contents=form_command_contents(data),
        command_name=command,
        timestamp=timestamp,
    )
    return model_message


async def is_phone_banned(phone: str) -> db.AuthBannedPhone | None:
    session: AsyncSession = db.DatabaseApi().cur_session

    query: sqlalchemy.Select = sqlalchemy.select(db.AuthBannedPhone). \
        where((db.AuthBannedPhone.phone == phone) & (datetime.datetime.now() < db.AuthBannedPhone.end)).limit(1)

    return await session.scalar(query)


def ban_phone(phone: str, duration: datetime.timedelta, reason: str) -> db.AuthBannedPhone:
    session: AsyncSession = db.DatabaseApi().cur_session
    
    logging.debug(f"Banning phone {phone} for {duration} due to {reason!r}")

    ban: db.AuthBannedPhone = db.AuthBannedPhone(phone=phone,
                                                 start=datetime.datetime.now(),
                                                 end=datetime.datetime.now() + duration,
                                                 reason=reason)
    session.add(ban)
    return ban


async def get_latest_code(phone: str) -> db.AuthCode | None:
    session: AsyncSession = db.DatabaseApi().cur_session

    query: sqlalchemy.Select = sqlalchemy.select(db.AuthCode). \
        where((db.AuthCode.phone == phone) &
              sqlalchemy.not_(db.AuthCode.used) &
              (datetime.datetime.now() < db.AuthCode.expires_at)) \
        .order_by(db.AuthCode.expires_at.desc()).limit(1)

    return await session.scalar(query)


async def check_code(phone: str, code: str) -> db.AuthCode | None:
    session: AsyncSession = db.DatabaseApi().cur_session

    query: sqlalchemy.Select = sqlalchemy.select(db.AuthCode) \
        .where((db.AuthCode.phone == phone) &
               (db.AuthCode.code == code) &
               sqlalchemy.not_(db.AuthCode.used) &
               (datetime.datetime.now() < db.AuthCode.expires_at)) \
        .limit(1)

    return await session.scalar(query)


async def get_auth_request(phone: str) -> db.AuthRequest | None:
    session: AsyncSession = db.DatabaseApi().cur_session

    query: sqlalchemy.Select = sqlalchemy.select(db.AuthRequest). \
        where((db.AuthRequest.status == "active") &
              (db.AuthRequest.phone == phone) &
              (datetime.datetime.now() < db.AuthRequest.expires_at))

    return await session.scalar(query)


# Check for active auth request (or create a new one)
async def ensure_for_auth_request(phone: str) -> db.AuthRequest:
    curr_time: datetime.datetime = datetime.datetime.now()
    session: AsyncSession = db.DatabaseApi().cur_session

    auth_request: db.AuthRequest | None = await get_auth_request(phone)

    if auth_request is not None:
        return auth_request

    auth_request = db.AuthRequest(phone=phone,
                                  created_at=curr_time,
                                  expires_at=curr_time + AUTH_REQUEST_VALID_TIME)

    session.add(auth_request)
    return auth_request


async def ensure_for_user(phone: str) -> db.User:
    session: AsyncSession = db.DatabaseApi().cur_session

    user: db.User | None = await db.DatabaseApi().find_user(own_phone=phone)
    if user is not None:
        return user

    user: db.User = db.User(own_phone=phone)
    session.add(user)
    return user


def call_info(call: db.Call, with_commands: bool = False) -> dict[str, typing.Any]:
    # TODO: More info?

    result: dict[str, typing.Any] = dict(
        id=call.uid,
        caller_number=call.caller_number,
        callee_number=call.callee_number,
        finished=call.finished,
        timestamp=str(call.timestamp),
    )

    if call.recording_url:
        result["recording_url"] = call.recording_url
    
    if call.timestamp:
        result["timestamp"] = call.timestamp.timestamp()
    
    if with_commands:
        result["commands"] = [
            call_command_info(command)
            for command in
            # TODO: Might be unnecessary...
            sorted(call.commands, key=lambda x: x.timestamp)
        ]

    return result


def call_command_info(command: db.Command) -> dict[str, typing.Any]:
    result: dict[str, typing.Any] = dict(
        id=command.uid,
        timestamp=command.timestamp.timestamp(),
        command_name=command.command_name,
        contents=command.contents,
    )
    
    return result


def sms_info(sms: db.SMS) -> dict[str, typing.Any]:
    # TODO: More info?

    result = dict(
        id=sms.id,
        direction="incoming" if sms.is_incoming else "outgoing",
        from_phone=sms.from_phone,
        to_phone=sms.to_phone,
        text=sms.text,
    )
    
    if sms.timestamp:
        result["timestamp"] = sms.timestamp.timestamp()

    return result


async def find_active_call(user_id: int) -> db.Call | None:
    session: AsyncSession = db.DatabaseApi().cur_session

    query: sqlalchemy.Select = sqlalchemy \
        .select(db.Call).where((db.Call.user_id == user_id) & sqlalchemy.not_(db.Call.finished))\
        .order_by(db.Call.timestamp.desc())\
        .limit(1)
    return await session.scalar(query)


async def ensure_device(device_uuid: str | uuid.UUID, device_type: int | str) -> db.Device:
    session: AsyncSession = db.DatabaseApi().cur_session

    if isinstance(device_type, str):
        device_type = {
            "ios": 0,
            "android": 1,
        }[device_type]

    assert device_type in range(2)  # TODO: Change?

    device: db.Device | None = await session.scalar(
        sqlalchemy.select(db.Device).where(db.Device.device_uuid == device_uuid)
    )
    if device is not None:
        assert device.onesignal_device_type == device_type
        if not device.extra_data.setdefault("registered", False):
            await onesignal.onesignal_register_device(
                device_type=device.onesignal_device_type, 
                device_uuid=device.device_uuid
            )
        return device

    device = db.Device(
        device_uuid=device_uuid,
        onesignal_device_type=device_type,
        extra_data={"registered": False}
    )
    session.add(device)
    await onesignal.onesignal_register_device(
                device_type=device.onesignal_device_type, 
                device_uuid=device.device_uuid
            )

    return device


def get_current_devices_of(user: db.User) -> typing.Generator[db.Device, None, None]:
    """
    Note: not async!
    """

    # Ensure that a session is open
    db.DatabaseApi().cur_session

    for user_session in user.sessions:
        yield user_session.device


async def send_push_to_user(text: str, user: db.User) -> None:
    devices: list[uuid.UUID] = [d.device_uuid for d in get_current_devices_of(user)]

    logging.info(f"Sending push to user {user.id} ({user.get_pretty_name()})", extra=dict(
        notification_text=text,
        target_devices=devices,
    ))
    
    await onesignal.onesignal_send_push(text, devices)


async def transform_transcript_to_messages(text: str, session_id: str = None) -> list:
    command_list = []

    async with db.DatabaseApi().session() as session:
        call_object = await db.DatabaseApi().get_call_object(session_id=session_id)
        call_start_time = call_object.timestamp
        call_id = str(call_object.uid)
        lines = text.split('\n')[0:-1]
        for line in lines:
            words = line.split(' ')
            side = words[0]
            match side:
                case 'Right':
                    side = 'user'
                case 'Left':
                    side = 'callee'
            end_time = words[3].split(':')
            replica_timestamp = call_start_time + datetime.timedelta(hours=float(end_time[0]),
                                                                     minutes=float(end_time[1]),
                                                                     seconds=float(end_time[2]))
            text = ' '.join(words[5:])
            message_type = 'whole'
            message_content = dict(side=side, text=text, type=message_type)
            command_name = 'message'
            command_id = str(uuid.uuid4())
            command = db.model.Command(uid=command_id,
                                       call_uid=call_id,
                                       timestamp=replica_timestamp,
                                       command_name=command_name,
                                       contents=message_content)
            session.add(command)

            command = dict(uid=command_id,
                           call_uid=call_id,
                           timestamp=str(replica_timestamp),
                           command_name=command_name,
                           contents=message_content)
            command_list.append(command)

        call_object.extra_data = command_list

    return command_list


async def get_transcript_commands(callback: dict) -> None:
    transcript_data = callback.get('transcription_complete')
    if transcript_data is None:
        logging.warning('Transcription_complete is None')
        return

    session_id = str(transcript_data.get('call_session_history_id'))
    if session_id is None:
        logging.warning('Session Id is None')
        return

    record_url = transcript_data.get('record_url')

    transcript_text = await voximplant.client.get_transcript(session_id=session_id)

    commands: list = await transform_transcript_to_messages(transcript_text, session_id)

    async with db.DatabaseApi().session():
        call_object = await db.DatabaseApi().get_call_object(session_id=session_id)
        call_id = call_object.uid
        user_id = call_object.user_id
        #record_url = call_object.recording_url
        call_object.recording_url = record_url
        user = await db.DatabaseApi().find_user(user_id=user_id)
        telegram_id = user.telegram_id


    from ..telegram.main import finish_outbound_call as tg_finish_outbound_call
    await tg_finish_outbound_call(call_id, telegram_id, commands, record_url)


async def process_incoming_sms(callback: dict) -> None:
    sms_inbound = callback["sms_inbound"]
    source_number = sms_inbound['source_number']
    destination_number = sms_inbound['destination_number']
    sms_body = sms_inbound['sms_body']
    async with db.DatabaseApi().session(allow_reuse=True):
        user: db.User | None = await db.DatabaseApi().find_user(given_phone=destination_number)

        # TODO: Extract timestamp from callback?
        await db.DatabaseApi().put_sms(db.SMS(user_id=user.id,
                                              is_incoming=True,
                                              from_phone=source_number,
                                              to_phone=destination_number,
                                              text=sms_body,
                                              timestamp=datetime.datetime.now()))

        if user is None:
            logging.warning(f'Got SMS to non-given number: {sms_inbound}')
        else:
            await handle_incoming_sms(user.id, source_number, sms_body)


async def handle_incoming_sms(user_id: int, from_phone: str, sms_body: str) -> None:
    # This doesn't incur any significant performance penalties
    # due to module caching. And it certainly is better than
    # introducing a new submodule.
    from ..telegram.main import \
        show_incoming_message_to_user as tg_show_incoming_message_to_user, \
        unpaid_incoming_sms_notification as tg_unbilled_incoming_sms_notification

    async with db.DatabaseApi().session():
        user: db.User | None = await db.DatabaseApi().find_user(user_id=user_id)
        assert user is not None

        logging.debug(f'Got SMS for user {user.id} ({user.get_pretty_name()})', extra=dict(
            from_phone=from_phone,
            sms_body=sms_body,
        ))

        billed = await bill(user, charge_msg=True)

        if user.telegram_id is not None:
            if billed:
                await tg_show_incoming_message_to_user(user.telegram_id, from_phone, sms_body)
            else:
                await tg_unbilled_incoming_sms_notification(user.telegram_id, from_phone)

        # TODO: else?
        if billed:
            await send_push_to_user(f"Получено сообщение от {from_phone}", user)

        await handle_advance_service(user_id, charge_msg=True)


async def save_call_recording(call_id: str | uuid.UUID, recording_url: str) -> None:
    if isinstance(call_id, str):
        call_id = uuid.UUID(call_id)
    
    async with db.DatabaseApi().session():
        call = await db.DatabaseApi().get_call_object(call_id=call_id)
        call.recording_url = recording_url
        del call
    
    asyncio.ensure_future(_do_save_call_recording(call_id, recording_url))


async def _do_save_call_recording(call_id: str | uuid.UUID, recording_url: str) -> None:
    public_url: str = await CloudStorageAPI().secure_upload_publish(f"recordings/{call_id}.mp3", url=recording_url)
    
    async with db.DatabaseApi().session():
        call = await db.DatabaseApi().get_call_object(call_id=call_id)
        call.recording_url = public_url


async def get_user_config(user: db.User) -> typing.Mapping[str, typing.Any]:
    """
    Returns a read-only proxy to the user's preferences.
    """
    
    return MappingProxyType(user.preferences.get_values())


async def update_user_config(user: db.User, values: dict[str, typing.Any]) -> None:
    """
    Updates the user's preferences with the given values.
    
    If a value is None, resets it to the default.
    """
    
    session: AsyncSession = db.DatabaseApi().cur_session
    
    if user.preferences.id != 0:
        user.preferences.values_override.update(values)
        user.preferences.values_override = {
            key: value
            for key, value in user.preferences.values_override.items()
            if value is not None
        }
        db.flag_modified(user.preferences, "values_override")
        session.add(user.preferences)
        return
    
    # Note: https://stackoverflow.com/questions/70405395/why-is-sqlalchemy-not-setting-default-value-correctly
    new_prefs = db.Preferences(
        values_override=values,
    )
    user.preferences = new_prefs
    session.add(new_prefs)


async def update_user_ignore_list(user: db.User, *phone_numbers: str, action: typing.Literal["add", "remove"]) -> None:
    """
    Updates the user's ignore list with the given phone numbers.
    """
    
    session: AsyncSession = db.DatabaseApi().cur_session
    
    assert action in ("add", "remove"), f"Invalid action: {action!r}"
    
    ignore_list: set[str] = set((await get_user_config(user))["IGNORE_LIST"])
    
    handle = ignore_list.add if action == "add" else ignore_list.discard
    
    for phone_number in phone_numbers:
        handle(normalize_phone(phone_number))
    
    await update_user_config(user, {"IGNORE_LIST": list(ignore_list)})


async def get_global_config() -> db.Preferences:
    """
    Returns the default preferences for all users.
    
    Note that the returned object may be modified.
    """
    
    return await db.DatabaseApi().cur_session.get(db.Preferences, 0)


async def reset_global_config() -> None:
    """
    Resets the global preferences to some hardcoded defaults.
    """
    
    # Ensure that a session is open
    session = db.DatabaseApi().cur_session
    
    preferences: db.Preferences = await get_global_config()
    
    preferences.values_override = dict(
        _comment="Put default preferences here manually",
        VOX_VOICE="default_female",
        VOX_GREETING=texts.GREETING_DEFAULT,
        VOX_CANT_HEAR_GOODBYE=dict(text="Не могу разобрать, что вы говорите. Попробуйте перезвонить"),
        VOX_CANT_HEAR_LIST=[dict(text="Вас не слышно"), dict(text="Я вас не слышу"), dict(text="Говорите громче")],
        VOX_FIRST_REPLICA=dict(text="Алло"),
        VOX_WELCOME_LIST=[dict(text="Снова привет"), dict(text="Здравствуйте"), dict(text="Доброго времени суток")],
        VOX_WHO_ARE_YOU_LIST=[dict(text="Я голосовой помощник. Я передам всё, что Вы скажете")],
        VOX_CALL_USER_LIST=[dict(text="Не могу, он сейчас занят. Я всё передам")],
        VOX_ORDER_LIST=[dict(text="Я робот и не могу дать подтверждение")],
        VOX_SPAM_LIST=[dict(text="Уважаемый, вы не ошиблись? Это прокуратура"), dict(text="Ваше предложение выглядит не очень полезным, но я всё равно его передам. До свидания")],
        VOX_DELIVERY_LIST=[dict(text="Извините, но я всего лишь помощник. Давайте я передам абоненту, что вы звонили, и он вам перезвонит?")],
        VOX_BANK_LIST=[dict(text="Статья 159.3 УК РФ предусматривает уголовную ответственность за мошенничество с использованием платежных карт и наказание в виде свободы на срок до десяти лет со штрафом в размере до одного миллиона рублей"), dict(text="Отлично, я как раз существую для защиты от телефонных мошенников")],
        VOX_GOODBYE_LIST=[dict(text="До свидания!"), dict(text="Пока")],
        VOX_RECRUITMENT_OFFICE_LIST=[dict(text="Я передам абоненту, что вы звонили. До свидания")],
        VOX_UNKNOWN_LIST=[dict(text="Окей, я всё передам"), dict(text="Хорошо, я всё передам")],
        VOX_BUSY=dict(text="Извините, но абонент сейчас занят. Я передам, что вы звонили. Хотите ли вы передать что-нибудь еще?"),
        VOX_BUSY_GOODBYE_LIST=[dict(text="Спасибо, хорошего дня")],
        VOX_BUSY_UNKNOWN_LIST=[dict(text="Всё записано"), dict(text="Хотите ещё что-нибудь передать?")],
        VOX_RECALL=dict(text="К сожалению, абонент сейчас занят. Я передам, что вы звонили, и он вам перезвонит"),
        VOX_BUSY_CANT_HEAR_LIST=[dict(text="Один момент, пожалуйста"), dict(text="Подождите еще немного, уточняю")],
        VOX_END_LOOP_GOODBYE_LIST=[dict(text="До свидания!"), dict(text="Пока"), dict(text="Приятно было пообщаться"), dict(text="Всего доброго")],
        VOX_END_LOOP_UNKNOWN_LIST=[dict(text="Хорошо, я всё записываю"), dict(text="Хотите передать что-нибудь ещё"), dict(text="Я слушаю"), dict(text="Вы можете рассказать мне всё, а я передам"), dict(text="Может что-то ещё?")],
        VOX_LONG_CALL=dict(text="Слишком долгий звонок. До свидания"),
        VOX_CONNECTION=dict(text="Соединяю с абонентом"),
        VOX_CONNECTION_FAILED=dict(text="Не получилось дозвониться. До свидания"),
        USER_DISPLAY_NAME="абонент",
        IGNORE_LIST=[],
        CHATGPT_AVAILABLE=True,
        CHATGPT_ENABLED=False,
        CHATGPT_INSTRUCTIONS="Если это важный звонок, попроси их перезвонить или передать сообщение. Иначе сбрось трубку.",
    ) | assistant_config.generate_replicas_customization("абонент")
    
    db.flag_modified(preferences, "values_override")
    session.add(preferences)


def normalize_phone(phone_number: str) -> str:
    """
    Takes a number in an arbitrary format, strips everything but digits and
    replaces the leading 7 or 8 with +7.
    """

    _orig = phone_number
    phone_number = re.sub(r"\D", "", phone_number)
    if phone_number.startswith("7") or phone_number.startswith("8"):
        phone_number = "+7" + phone_number[1:]
    if not phone_number.startswith("+7") or len(phone_number) not in range(10, 13):
        logging.warn(f"Potentially invalid phone number: {_orig} (-> {phone_number})")
    
    return phone_number


async def bill(user: db.User, charge_call: bool = False, charge_msg: bool = False) -> bool:
    # Ensure that a session is open
    db.DatabaseApi().cur_session
    
    logging.debug(f"Charging user {user.id} ({user.get_pretty_name()}): {charge_call=}, {charge_msg=}")

    if user.subscription is None:
        return False

    if ExtraData.ADVANCED_SERVICE_STATE not in user.extra_data:
        user.extra_data = user.extra_data | {ExtraData.ADVANCED_SERVICE_STATE: AdvanceServiceState.UNUSED}

    active_plan: db.ActivePlan | None = await get_active_plan(user, need_calls=charge_call, need_messages=charge_msg)
    if active_plan is None:
        if not user.extra_plan_autocharge:
            return False

        if user.extra_data[ExtraData.ADVANCED_SERVICE_STATE] != AdvanceServiceState.UNUSED:
            return False

        # Provide one call in advance
        user.extra_data = user.extra_data | {ExtraData.ADVANCED_SERVICE_STATE: AdvanceServiceState.IN_PROGRESS}
    else:
        if charge_call:
            logging.info("-1 call")
            active_plan.calls_left -= 1

        if charge_msg:
            logging.info("-1 msg")
            active_plan.messages_left -= 1

        user.extra_data = user.extra_data | {ExtraData.ADVANCED_SERVICE_STATE: AdvanceServiceState.UNUSED}

    return True


async def handle_advance_service(user_id: int, charge_call: bool = False, charge_msg: bool = False):
    from ..telegram.main import \
        successful_payment as tg_successful_payment, \
        unsuccessful_payment as tg_unsuccessful_payment

    async with db.DatabaseApi().session(allow_reuse=True):
        user = await db.DatabaseApi().find_user(user_id=user_id)

        if user.extra_data[ExtraData.ADVANCED_SERVICE_STATE] != AdvanceServiceState.IN_PROGRESS:
            return

        extra_plan: db.Plan = await db.DatabaseApi().get_plan(plan_id=Plans.EXTRA)
        plan_id = extra_plan.id
        plan_price = extra_plan.price

        # Just to get end date
        active_plan: db.ActivePlan | None = await get_active_plan(user)
        if active_plan is None:
            user.extra_data = user.extra_data | {ExtraData.ADVANCED_SERVICE_STATE: AdvanceServiceState.NOTIFIED}

        # Charge for extra plan
        try:
            tx = await cp_methods.charge(user, extra_plan, cp_types.PaymentReasons.EXTRA_PLAN)
            await activate_extra_plan(user, extra_plan, payment_id=tx.transaction_id)

            active_plan: db.ActivePlan | None = await get_active_plan(user, need_calls=charge_call, need_messages=charge_msg)
            assert active_plan, "Plan should've been activated!"
            
            if charge_call:
                active_plan.calls_left -= 1

            if charge_msg:
                active_plan.messages_left -= 1

            extra_plan_success = True
        except CpPaymentError as e:
            # TODO: ?
            logging.info(f"Payment error: {e}")
            
            next_try_time = datetime.datetime.combine(datetime.date.today(), BILLING_TIME) \
                            + CHARGE_RETRY_PERIOD
            # next_try_time = datetime.datetime.now() + common.CHARGE_RETRY_PERIOD

            # TODO: Ugly hack, I am absolutely not sure if it works
            plan_end: datetime.datetime
            if active_plan is None:
                plan_end = datetime.datetime.now()
                logging.warning("Active plan is None, not sure how to handle...")
            else:
                plan_end = active_plan.end
            
            
            await billing_actions.ExtraPlanPaymentRetryAction(
                user_id,
                deadline=plan_end.isoformat(),
                retries_left=CHARGE_RETRIES_COUNT,
            ).schedule(next_try_time)

            await billing_actions.ExtraPlanResetAction(
                user_id,
            ).schedule(plan_end)

            user.extra_data = user.extra_data | {ExtraData.FAILED_EXTRA_RECOVERED: False}
            extra_plan_success = False

        user.extra_data = user.extra_data | {ExtraData.ADVANCED_SERVICE_STATE: AdvanceServiceState.NOTIFIED}
        telegram_id = user.telegram_id

    if user.telegram_id is not None:
        try:
            if extra_plan_success:
                await tg_successful_payment(telegram_id, plan_id, plan_price)
            else:
                await tg_unsuccessful_payment(telegram_id, plan_id, plan_price, is_extra=True)
        except aiogram_exceptions.BadRequest as e:
            logging.error("Failed to inform user of payment status", extra=dict(
                error=e,
                user_id=user.id,
                user_name=user.get_pretty_name(),
                plan_id=plan_id,
                extra_plan_success=extra_plan_success,
            ))

    # TODO: onesignal event?


async def get_unpaid_status(user: db.User) -> int:
    # Ensure that a session is open
    db.DatabaseApi().cur_session

    if user.subscription is None:
        return UnpaidStatus.NO_SUBSCRIPTION

    active_plans = await get_active_plans(user)
    if len(active_plans) == 0:
        # No active plans but active subscription means that
        return UnpaidStatus.SUBSCRIPTION_UNPAID

    if user.extra_plan_autocharge:
        return UnpaidStatus.OUT_OF_PLAN_EXTRA_UNPAID
    else:
        return UnpaidStatus.OUT_OF_PLAN_NO_EXTRA_AUTOCHARGE


# TODO: Merge with normalize_number?
def strip_number(number: str) -> str:
    orig_number: str = number
    
    number = number.translate({ord(i): None for i in '()- '})
    if len(number) <= 10:
        # [+7][8888888888]
        # if the +7/7/8 prefix is missing, only 10 digits would remain
        # seems to happen occasionally with voximplant
        number = '7' + number
    elif len(number) >= 14:
        # [810][7][8888888888]
        number = number.removeprefix('810')
    
    if number.startswith('+7'):
        number = number[1:]
    elif number.startswith('8'):
        number = '7' + number[1:]
    elif number.startswith('7'):
        if len(number) == 12:
            number = '7' + number[2:]
    else:
        logging.warning(f"Bad number: {orig_number!r}")
    
    return number

def prettify_number(number: str) -> str:
    if not (len(number) == 11 and number.startswith('7')):
        logging.warning(f"Bad stripped number: {number!r}")
        return number
    
    return f"+7 ({number[1:4]}) {number[4:7]}-{number[7:9]}-{number[9:11]}"


# TODO: Add __all__?