from __future__ import annotations
import typing

import aiogram
import aiohttp
import aiofiles
import asyncio

from pydub import AudioSegment
import json
import os
import traceback
from datetime import datetime
import logging
import uuid
import warnings
import contextlib
import dataclasses
import io
import itertools
import phonenumbers

from pymorphy2 import MorphAnalyzer
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from aiogram.dispatcher.filters import Text
from aiogram.dispatcher.middlewares import BaseMiddleware
from aiogram.dispatcher.storage import BaseStorage
from aiogram.dispatcher.dispatcher import log as dispatcher_logger
from aiogram.contrib.fsm_storage.redis import RedisStorage2
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import StatesGroup, State
from aiogram.utils import exceptions
from aiogram.types.message import ParseMode
from aiogram.types import InputFile

from ..api.command_queues import tg_call_commands_queues
from .keyboards import *
from .. import db
from .. import common
from ..common import strip_number, prettify_number
from ..common.assistant_config import ASSISTANT_VOICES
from ..cloud_storage import CloudStorageAPI
from .. import voximplant
from .. import amoCRM
from ..api.cloudpayments import methods as cp_methods
from ..api.cloudpayments import types as cp_types
from . import texts
from ..common.extra_data_utils import UserFreeTrialUtil

# TODO: Encapsulate these, instead of using bare globals?
storage: BaseStorage
bot: Bot
dp: Dispatcher
locks: typing.Dict[str, asyncio.Lock]


class _DispatcherLogFilter:
    """
    A logger filter used to lower the severity of a specific
    log message.

    Note that since filters are applied after level check,
    the new level will ignore the logger's configured minimum.
    """

    # If the message contains a specified substring, the log level will be changed
    changed_levels: dict[str, int]

    def __init__(self, changed_levels: dict[str, int]) -> None:
        self.changed_levels = changed_levels

    def filter(self, record: logging.LogRecord) -> bool:
        for pattern, level in self.changed_levels.items():
            if pattern not in record.msg:
                continue

            record.levelno = level
            record.levelname = logging.getLevelName(level)

        return True


async def run() -> None:
    async with contextlib.AsyncExitStack() as stack:
        import config
        global storage, bot, dp, locks

        if config.TELEGRAM_BOT_SECRET is None:
            logging.error("No Telegram bot token specified. Stopping.")
            return

        if config.DB_HOST is None or config.DB_NAME is None:
            warnings.warn("No Postgres URL specified. Falling back to memory storage.")
            storage = MemoryStorage()
        else:
            storage = db.DatabaseStorage()
        stack.push_async_callback(storage.close)

        bot = Bot(config.TELEGRAM_BOT_SECRET, parse_mode='HTML')
        stack.push_async_callback(bot.close)

        locks = {}
        dp = Dispatcher(bot, storage=storage)
        stack.push_async_callback(dp.wait_closed)
        stack.callback(dp.stop_polling)

        dp.middleware.setup(RecorderMiddleware())

        postponed.apply_all()

        dispatcher_logger.addFilter(
            _DispatcherLogFilter(
                {
                    # This error happens when telegram servers are down.
                    # Nothing we can do about it, really
                    "Cause exception while getting updates.": logging.WARNING,
                }
            )
        )

        logging.info("Telegram bot started")

        # TODO: Suppress or lower logging of exceptions with connecting to Telegram API servers
        await dp.skip_updates()
        await dp.start_polling()


__all__ = [
    "run",
    "start_dialog",
    "process_command",
    "finish",
    "successful_subscription",
    "successful_payment",
    "unsuccessful_payment",
    "successful_payment_retry",
    "user_kicked",
    "user_unsubscribed_ext",
]


class postponed:
    """
    A decorator to postpone callback registration until the initialization stage.
    """

    # Not needed for any of aiogram's decorators, since those return the same object they take
    REASSIGN_GLOBALS: typing.Final[bool] = False

    todo: typing.ClassVar[list[postponed]] = []

    decorator: str | typing.Callable
    args: typing.Sequence[typing.Any]
    kwargs: typing.Mapping[str, typing.Any]
    func: typing.Callable

    def __init__(self, decorator: str | typing.Callable, *args, **kwargs):
        assert isinstance(decorator, str) or callable(
            decorator
            ), f"Decorator must be a string or a callable, not {type(decorator)}"
        self.decorator = decorator
        self.args = args
        self.kwargs = kwargs

    def __call__(self, func: typing.Callable) -> typing.Callable | None:
        self.func = func
        self.todo.append(self)

        if self.REASSIGN_GLOBALS:
            # To forbid use before init
            return None

        return func

    def apply(self) -> None:
        global dp
        if isinstance(self.decorator, str):
            decorator = getattr(dp, self.decorator)(*self.args, **self.kwargs)
        else:
            decorator = self.decorator(dp, *self.args, **self.kwargs)

        result = decorator(self.func)

        if self.REASSIGN_GLOBALS:
            globals()[self.func.__name__] = result
        else:
            assert result is self.func, f"Decorator changed function: {self.func.__qualname__}"

    @classmethod
    def apply_all(cls) -> None:
        for item in cls.todo:
            item.apply()

        cls.todo.clear()


# region states
class Registration(StatesGroup):
    GetNumber = State()
    Onboarding = State()


class TariffPurchase(StatesGroup):
    ChooseTariffConfirmation = State()
    UnsuccessfulPayment = State()


class ProfileCall(StatesGroup):
    GetCallNumber = State()
    GetConfirm = State()


class ProfileSendMessage(StatesGroup):
    GetMessageNumber = State()
    GetConfirmNumber = State()
    GetMessage = State()
    GetConfirmMessage = State()


class ProfileIncomingCall(StatesGroup):
    LineIsBusy = State()


class Customization(StatesGroup):
    Menu = State()
    Greeting = State()
    Name = State()
    Ignorelist = State()
    IgnorelistAdd = State()
    ChatGPTInstructions = State()
# endregion

# region synchrohization structure
@dataclasses.dataclass
class MultipleCallSynchronizationData:
    STORAGE_KEY = "multiple_calls_synchronization"

    lock_id: str
    calls_count: int
    is_all_calls_finished: bool

    # region serialization
    @classmethod
    def from_json(cls, json: dict[str, typing.Any]) -> MultipleCallSynchronizationData:
        return MultipleCallSynchronizationData(**json)

    def to_json(self) -> dict[str, typing.Any]:
        return dataclasses.asdict(self)

    @classmethod
    def state_load(cls, data: typing.Mapping[str, typing.Any], telegram_id: str) -> MultipleCallSynchronizationData:
        if cls.STORAGE_KEY not in data:
            (MultipleCallSynchronizationData.create(telegram_id)).state_store(data)

        return MultipleCallSynchronizationData.from_json(data[cls.STORAGE_KEY])

    def state_store(self, data: typing.MutableMapping[str, typing.Any]) -> None:
        data[self.STORAGE_KEY] = self.to_json()

    @classmethod
    def state_del(cls, data: typing.MutableMapping[str, typing.Any]) -> None:
        data.pop(cls.STORAGE_KEY, None)

    @classmethod
    @contextlib.asynccontextmanager
    async def in_state(cls, data: typing.MutableMapping[str, typing.Any], telegram_id: str, state: FSMContext) -> typing.AsyncGenerator[MultipleCallSynchronizationData, None]:
        lock: asyncio.Lock = locks.setdefault(telegram_id, asyncio.Lock())
        try:
            await lock.acquire()
            obj: MultipleCallSynchronizationData = cls.state_load(data, telegram_id)
            yield obj
        finally:
            if obj.is_all_calls_finished:
                MultipleCallSynchronizationData.state_del(data)
                await state.finish()
            else:
                obj.state_store(data)
            lock.release()
    # endregion serialization
        
    @classmethod
    def create(cls, lock_id: str, calls_count: int = 0) -> MultipleCallSynchronizationData:
        return MultipleCallSynchronizationData(
            lock_id=lock_id,
            calls_count=calls_count,
            is_all_calls_finished=False,
        )
# endregion synchronization struct

async def get_user(obj: types.Message | types.CallbackQuery | int, *,
                   must_exist: bool = True,
                   **kwargs) -> db.User | None:
    user: db.User | None
    user_id: str

    if isinstance(obj, int):
        user_id = str(user)
    else:
        user_id = str(obj.from_user.id)

    user = await db.DatabaseApi().find_user(telegram_id=user_id, **kwargs)

    if must_exist and user is None:
        raise ValueError(f"User with telegram_id={user_id} not found in database.")

    return user


async def get_plan_by_id(plan_id: int, must_exist: bool = True) -> db.Plan | None:
    plan: db.Plan | None = await db.DatabaseApi().get_plan(plan_id=plan_id)

    if must_exist and plan is None:
        raise ValueError(f"Plan with plan_id={plan_id} not found in database.")

    return plan


async def add_user(obj: types.Message | types.CallbackQuery, user: db.User) -> None:
    assert (await get_user(obj, must_exist=False)) is None, "User already exists somehow..."

    await common.add_user(user)


async def change_subscription(
    obj: types.Message | types.CallbackQuery,
    plan_id: int,
    payment_id: int | None,
    free_trial: bool = False,
) -> str:
    """
    Returns: virtual number
    """
    user: db.User = await get_user(obj)
    plan: db.Plan = await get_plan_by_id(plan_id)

    return await common.change_subscription(user, plan, payment_id, free_trial=free_trial)


async def unsubscribe(obj: types.Message | types.CallbackQuery, **kwargs) -> None:
    user: db.User = await get_user(obj)
    await common.unsubscribe(user, **kwargs)


async def record_message(msg: types.Message | typing.Awaitable[types.Message],
                         response_to: types.Message | None) -> None:
    """
    Store a copy of the message in the database for debug.
    
    `origin` is either None if it's a message from the user, or the message we're replying to.
    # TODO: Support explicitly specifying a user id or something
    Note that it shouldn't have a default value of None, since in most cases it must be explicitly set!
    
    Since this method is a very commonly used helper, it automatically
    accounts for opening a database session, if necessary, and awaiting msg if it hasn't been awaited yet.
    The auto-opening a session is fine only because this method doesn't return any database objects.
    """

    if hasattr(msg, "__await__"):
        msg = await msg
    assert isinstance(msg, types.Message)

    from_us: bool = response_to is not None

    async with db.DatabaseApi().session(allow_reuse=True):
        # Note: if this line fails, it's probably because you passed None for response_to,
        # while msg is sent by us.
        user: db.User = await get_user(response_to or msg, must_exist=False)

        message = db.TgMessage(
            tg_chat_id=msg.chat.id,
            tg_message_id=msg.message_id,
            from_us=from_us,
            data=dict(
                msg=msg.to_python(),
            )
        )

        if user:
            user.tg_messages.add(message)
        else:
            db.DatabaseApi().cur_session.add(message)


async def answer_message(message: types.Message,
                         *args,
                         **kwargs) -> types.Message:
    """
    Same as `message.answer`, but also records the message in the database.
    """

    response: types.Message = await message.answer(*args, **kwargs)

    await record_message(response, response_to=message)

    return response


async def send_message(chat_id, *args, **kwargs) -> types.Message:
    """
    Same as `bot.send_message`, but also records the message in the database.
    """
    response: types.Message = await bot.send_message(chat_id=chat_id, *args, **kwargs)
    
    user: db.User | None
    user_id: str = str(chat_id)
    tg_chat_id = int(chat_id)
    

    async with db.DatabaseApi().session(allow_reuse=True):
        user = await db.DatabaseApi().find_user(telegram_id=user_id)

        if user is None:
            raise ValueError(f"User with telegram_id={user_id} not found in database.")

        message = db.TgMessage(
            tg_chat_id=tg_chat_id,
            tg_message_id=response.message_id,
            from_us=True,
            data=dict(
                msg=response.to_python(),
            )
        )

        if user:
            user.tg_messages.add(message)
        else:
            db.DatabaseApi().cur_session.add(message)
    return response


async def check_outgoing(obj: types.Message | types.CallbackQuery,
                         **kwargs) -> typing.Tuple[bool, db.ActivePlan]:
    user: db.User = await get_user(obj)
    has_virtual_number = user.given_phone != ""
    active_plan: db.ActivePlan | None = await common.get_active_plan(user, **kwargs)

    has_advance = common.ExtraData.ADVANCED_SERVICE_STATE in user.extra_data and \
                  user.extra_data[common.ExtraData.ADVANCED_SERVICE_STATE] == common.AdvanceServiceState.UNUSED
    has_service = active_plan is not None or has_advance
    return has_virtual_number, has_service


class RecorderMiddleware(BaseMiddleware):
    async def on_process_message(self, message: types.Message, data: dict):
        async with db.DatabaseApi().session():
            await record_message(message, response_to=None)


@postponed(Dispatcher.errors_handler)
async def errors_handler(update: types.Update, error: Exception) -> bool:
    if isinstance(error, aiogram.exceptions.TelegramAPIError):
        logging.info("TelegramAPIError", extra=dict(update=update, error=traceback.format_exception(error)))
        return True  # Error not critical, let's say
    else:
        logging.error("Update caused error", extra=dict(update=update, error=traceback.format_exception(error)))
        return False  # Error not handled, let's say


@postponed(Dispatcher.message_handler, commands=['reset'], state='*')
async def reset_status(message: types.Message, state: FSMContext):
    async with db.DatabaseApi().session():
        user: db.User | None = await db.DatabaseApi().find_user(telegram_id=message.from_user.id)
    if user is not None:
        logging.info(f"Reset state for user {user.id} ({user.get_pretty_name()})")
    await state.finish()
    await message.delete()


@postponed(
    Dispatcher.message_handler, commands=['start'], state=[None, Registration.GetNumber, Registration.Onboarding]
    )
async def start_command(message: types.Message, state: FSMContext):
    async with db.DatabaseApi().session() as session:
        user: db.User | None = await get_user(message, must_exist=False)

        if user is None:
            session.add(
                db.User(
                    telegram_id=str(message.from_user.id),
                    first_name=str(message.from_user.first_name),
                    last_name=str(message.from_user.last_name),
                    own_phone=str(uuid.uuid4())[:11],
                )
            )

            import config
            if config.BRANCH == 'master':
                async with aiohttp.ClientSession() as client_session:
                    username = message.from_user.username
                    if username is None or username == '':
                        fullname = message.from_user.full_name
                        body = {'username': fullname}
                    else:
                        body = {'username': f'@{username}'}

                    api_key = config.API_KEY
                    headers = {"Content-Type": "application/json; charset=utf-8"}
                    async with client_session.post(
                        url=f'https://test.busy.contact/logs/users?apiKey={api_key}', headers=headers,
                        data=json.dumps(body)
                        ) as r:
                        if r.status != 200:
                            logging.error("Unsuccessful post-request about new user")

            try:
                amo_contact_object = amoCRM.entities.get_contact_object(
                    message.from_user.first_name,
                    message.from_user.last_name,
                    f'@{message.from_user.username}'
                    )
                amo_contact_info = await amoCRM.client.create_contacts(amo_contact_object)
                amo_contact_id = amoCRM.entities.get_new_contact_id(amo_contact_info)

                amo_lead_object = amoCRM.entities.get_lead_object(contact_id=amo_contact_id)
                amo_lead_info = await amoCRM.client.create_leads(amo_lead_object)
                amo_lead_id = amoCRM.entities.get_new_lead_id(amo_lead_info)
                await amoCRM.client.update_contacts(
                    amoCRM.entities.get_updating_lead_contact(amo_contact_id, amo_lead_id)
                    )

                user = await db.DatabaseApi().find_user(telegram_id=message.from_user.id)
                session.add(db.AmoContact(id=amo_contact_id, busy_user_id=user.id))
                session.add(db.AmoLead(id=amo_lead_id, contact_id=amo_contact_id))
            except Exception:
                logging.warning("AmoCRM error", exc_info=True)

        if user is not None and len(user.active_plans) > 0:
            given_phone = user.given_phone
            reply_markup: ReplyKeyboardMarkup
            if given_phone == "":
                reply_markup = kb_main_without_number()
            else:
                reply_markup = kb_main_with_number()
            await answer_message(
                message, text=texts.ALREADY_REGISTERED,
                reply_markup=reply_markup
                )
            await message.delete()
            return

    await answer_message(message, text=texts.WelcomeMessage.MAIN_MESSAGE)
    async with aiofiles.open('app/telegram/picture/audio_video_example.JPEG', 'rb') as photo:
        await bot.send_photo(
            chat_id=message.from_user.id, photo=photo
        )
    await answer_message(message, text=texts.WelcomeMessage.NEED_START)
    await answer_message(message, text=texts.WelcomeMessage.CLICK_SHARE_NUMBER, reply_markup=kb_get_number())
    await Registration.GetNumber.set()
    await message.delete()


@postponed(Dispatcher.message_handler, commands=['revive'], state=[None])
async def revive(message: types.Message, state: FSMContext):
    if message.from_user.id != 250777801:
        await answer_message(message, text="Недостаточно прав для выполнения команды")
        return

    users_to_revive = [250777801, 1460965865, 1479478225, 1360460980, 1256604317, 663521067, 970462715, 1201218484,
                       1027054250, 1153413272, 1018942406, 1071553097, 1011350398, 717444601, 886741615, 885255466,
                       871267622, 527538666, 1910361298, 985247659, 567431350, 463417123, 745877010, 441610185]
    async with db.DatabaseApi().session() as session:
        for tg_id in users_to_revive:
            user: db.User | None = await db.DatabaseApi().find_user(telegram_id=str(tg_id))
            if user is not None:
                logging.info(f"Revive: skipping tg id {tg_id} as user is already registered")
                continue

            session.add(
                db.User(
                    telegram_id=str(tg_id),
                    own_phone=str(uuid.uuid4())[:11]
                )
            )

            try:
                await send_message(
                    chat_id=tg_id, text="Во время презентации у нас все сломалось по причине \"кривые руки\".\n"
                                        "Просим простить нас и все-таки ознакомиться с сервисом :)"
                    )

                await send_message(chat_id=tg_id, text=texts.WelcomeMessage.MAIN_MESSAGE)
                await bot.send_photo(chat_id=tg_id, photo=open('app/telegram/picture/audio_video_example.JPEG', 'rb'))
                await send_message(chat_id=tg_id, text=texts.WelcomeMessage.NEED_START)
                await send_message(
                    chat_id=tg_id, text=texts.WelcomeMessage.CLICK_SHARE_NUMBER, reply_markup=kb_get_number()
                    )

                state = FSMContext(storage=dp.storage, chat=tg_id, user=tg_id)
                async with state.proxy() as data:
                    await state.set_state(Registration.GetNumber.state)

                logging.info(f"Revive: successfully revived tg id {tg_id}")
            except aiogram.utils.exceptions.BotBlocked:
                logging.info(f"Revive: tg id {tg_id} blocked our shitty bot :(")


@postponed(Dispatcher.message_handler, commands=['remind_inactivity'], state=[None])
async def remind_inactivity(message: types.Message, state: FSMContext):
    if message.from_user.id != 615388987:
        await answer_message(message, text="Недостаточно прав для выполнения команды")
        return

    telegram_ids = ['5671171751', '718616969', '6895018443', '6183390965', '2075337366', '5202589295', '1316222869', '6453626196', '394843', '1261286875', '5541886925', '5617088905', '6552172661', '6617934529', '29392', '6512539577', '6807931856', '53024025', '5986110', '150326793', '1613710605', '205595116', '928956614', '1248971328', '6234082894', '818361774', '186984830', '5917490426', '1046657294', '5380031486', '6358072338', '6406017569', '1245153183', '615388987', '700535048', '125036696', '5443289216', '1790069839', '5968460337', '2083215057', '6127749917', '1359131607', '5185894960', '6598759783', '6048012641', '6286716505', '1001329309', '5979596412', '682466923', '6209539474', '1253672370', '6695891595', '5736507426', '590968801', '5113924267', '393853114', '5233028294', '1893400559', '2072362801', '6052243638', '1386415161', '6051118476', '5403788138', '1971189479', '765052454', '1249165589', '5755341843', '5422851077', '748952226', '1423244485', '419998576', '5857210907', '6295376063', '6673309656', '1865244913', '6810923209', '6379315687', '6421084780', '6049325698', '414881347', '1281409963', '6109232816', '343909897', '6514912470', '1828505427', '811931790', '6545823267', '5391455637', '5601764820', '5881199946', '2038532961', '5200064718', '1435418186', '6919830747', '413343658', '1890133865', '2098150028', '1148769788', '5700358249', '5278521656', '5912165557', '1043305432', '5509701556', '1066507885', '1268436927', '5925013823', '1725308805', '1908034935', '6035379432', '6585851057', '1447450255', '6321725062', '831395772', '6801240709', '5034208775', '492335521', '6274994525', '6936353570', '1062974832', '1077735155', '5311374215', '477897602', '868129663', '1226162849', '1040062909', '744045170', '155872264', '6375626775', '594657666', '5611835532', '619176958', '825053203', '5322846637', '6301509719', '6408664429', '5832129164', '1480101016', '1226390011', '2077532546', '358313339', '1109278349', '6390097924', '6672526898', '770819716', '1415616806', '1120724898', '5755528166', '1468772013', '5274769393', '1268770251', '5701785202', '1642562340', '359821431', '580500040', '1835404718', '1187202299', '295384707', '5223379462', '823119376', '6100878795', '1412952281', '5095704468', '2082994048', '6320209739', '1064309655', '6132108985', '816009765', '5564902114', '5366300879', '6882516314', '6024893273', '5131524823', '6769457349', '1366058152', '6186399306', '1474985100']

    for tg_id in telegram_ids:
        try:
            await bot.send_photo(
                chat_id=tg_id,
                photo=InputFile(os.path.abspath('app/telegram/picture/busy.jpg')),
                caption=texts.BUSY_INFO_REMINDER_MESSAGE
            )
            logging.info(f"reminder: successfully sent to {tg_id=}")
        except Exception:
            logging.info(f"reminder: {tg_id=} not sent")


@postponed(Dispatcher.message_handler, content_types=types.ContentType.CONTACT, state=Registration.GetNumber)
async def get_number_handler(message: types.Message, state: FSMContext):
    if message.contact.user_id != message.from_user.id:
        await answer_message(message, texts.WRONG_NUMBER)
        return

    correct_number = message.contact.phone_number.removeprefix("+")
    async with db.DatabaseApi().session():
        user = await db.DatabaseApi().find_user(telegram_id=message.from_user.id)
        if user is not None:
            user.own_phone = correct_number

        try:
            user = await db.DatabaseApi().find_user(telegram_id=message.from_user.id)
            amo_contact: db.AmoContact = await db.DatabaseApi().get_amo_contact(user_id=user.id)
            amo_lead: db.AmoLead = await db.DatabaseApi().get_amo_lead(contact_id=amo_contact.id)
            amo_lead_new_status_object = amoCRM.entities.get_updating_lead_object(
                lead_id=amo_lead.id,
                status_id=amoCRM.entities.lead_bot_registry_id
                )
            await amoCRM.client.update_leads(amo_lead_new_status_object)
            await amoCRM.client.update_contacts(
                amoCRM.entities.get_updating_phone_contact(amo_contact.id, correct_number)
                )

            amo_lead.status_id = amoCRM.entities.lead_bot_registry_id
        except Exception:
            logging.warning("AmoCRM error", exc_info=True)

    await Registration.next()

    await answer_message(message, text=f'Ваш номер {prettify_number(correct_number)}', reply_markup=kb_welcome())

    await answer_message(message, text=texts.Tariff.FIRST_TARIFF_MESSAGE, reply_markup=ikb_tariff())

    photo_paths = [
        'app/telegram/picture/very_busy_description.jpg',
        'app/telegram/picture/super_busy_description.jpg',
        'app/telegram/picture/ultra_busy_description.jpg'
    ]
    for photo_path in photo_paths:
        with open(photo_path, 'rb') as photo:
            await bot.send_photo(chat_id=message.from_user.id, photo=photo)

    await answer_message(message, text=texts.Tariff.TARIFF_MESSAGE, reply_markup=ikb_tariff())

@postponed(Dispatcher.message_handler, state=Registration.GetNumber)
async def wrong_get_number(message: types.Message, state: FSMContext):
    if message.text[0] == '/':
        await answer_message(message, "Пока вы не можете пользоваться командами")
    await message.delete()


@postponed(Dispatcher.callback_query_handler, text='Choose tariff', state=Registration.Onboarding)
async def choose_tariff_command(callback: types.CallbackQuery, state: FSMContext):
    edited_msg = await bot.edit_message_text(
        chat_id=callback.from_user.id, message_id=callback.message.message_id,
        text=texts.Tariff.TARIFF_MESSAGE, reply_markup=ikb_tariff()
        )
    await record_message(edited_msg, callback.message)


@postponed(Dispatcher.message_handler, Text(equals="Выбрать тариф 💳"), state=Registration.Onboarding)
async def choose_tariff_command(message: types.Message, state: FSMContext):
    await answer_message(message, text=texts.Tariff.TARIFF_MESSAGE, reply_markup=ikb_tariff())


@postponed(Dispatcher.message_handler, commands=['tariffs'], state=Registration.Onboarding)
async def choose_tariff_command(message: types.Message, state: FSMContext):
    await answer_message(message, text=texts.Tariff.TARIFF_MESSAGE, reply_markup=ikb_tariff())


@postponed(Dispatcher.message_handler, Text(equals="Как работает Busy 🤔"), state=[None, Registration.Onboarding])
async def how_is_work_command(message: types.Message):
    await answer_message(message, text=texts.Help.INFO)
    await message.delete()


@postponed(Dispatcher.message_handler, commands=['help'], state=[None, Registration.Onboarding])
@postponed(Dispatcher.message_handler, Text(equals="Поддержка 👨‍💻"), state=[None, Registration.Onboarding])
async def help_command(message: types.Message):
    await answer_message(message, text=texts.Help.HELP, reply_markup=ikb_help())
    await message.delete()


@postponed(Dispatcher.callback_query_handler, text='Help Info', state=[None, Registration.Onboarding])
async def help_info_handler(callback: types.CallbackQuery, state: FSMContext):
    await answer_message(callback.message, text=texts.Help.INFO)
    await callback.answer()


@postponed(Dispatcher.callback_query_handler, text='Help Redirection Megafon', state=[None, Registration.Onboarding])
async def redirection_megafon_handler(callback: types.CallbackQuery, state: FSMContext):
    await send_message(
        chat_id=callback.from_user.id, text=texts.Help.REDIRECTION_MEGAFON,
        disable_web_page_preview=True
        )
    # await answer_message(callback.message, text=texts.Help.REDIRECTION_MEGAFON)
    await callback.answer()


@postponed(Dispatcher.callback_query_handler, text='Help Redirection MTS', state=[None, Registration.Onboarding])
async def redirection_mts_handler(callback: types.CallbackQuery, state: FSMContext):
    await answer_message(callback.message, text=texts.Help.REDIRECTION_MTS)
    await callback.answer()


@postponed(Dispatcher.callback_query_handler, text='Help Redirection Beeline', state=[None, Registration.Onboarding])
async def redirection_beeline_handler(callback: types.CallbackQuery, state: FSMContext):
    await answer_message(callback.message, text=texts.Help.REDIRECTION_BEELINE)
    await callback.answer()


@postponed(Dispatcher.callback_query_handler, text='Help Redirection Other', state=[None, Registration.Onboarding])
async def redirection_other_handler(callback: types.CallbackQuery, state: FSMContext):
    await answer_message(callback.message, text=texts.Help.REDIRECTION_OTHER)
    await callback.answer()


@postponed(Dispatcher.callback_query_handler, text='Help Tariff Info', state=Registration.Onboarding)
async def help_tariff_info_handler(callback: types.CallbackQuery, state: FSMContext):
    ikb = InlineKeyboardMarkup(row_width=1)
    ib_choose_tariff = InlineKeyboardButton(text="Выбрать тариф 💳", callback_data="Choose tariff")
    ikb.add(ib_choose_tariff)
    edited_msg = await callback.message.edit_text(text=texts.Help.TARIFF_INFO, reply_markup=ikb)
    await record_message(edited_msg, callback.message)
    await callback.answer()


@postponed(Dispatcher.callback_query_handler, text='Help Tariff Info')
async def help_tariff_info_handler(callback: types.CallbackQuery, state: FSMContext):
    ikb = InlineKeyboardMarkup(row_width=1)
    ib_change_tariff = InlineKeyboardButton(text="Сменить тариф 💳", callback_data="Change tariff")
    ikb.add(ib_change_tariff)
    edited_msg = await callback.message.edit_text(text=texts.Help.TARIFF_INFO, reply_markup=ikb)
    await record_message(edited_msg, callback.message)
    await callback.answer()


# @postponed(Dispatcher.callback_query_handler, text='Help Redirection', state=[None, Registration.Onboarding])
# async def redirection_handler(callback: types.CallbackQuery, state: FSMContext):
#     await callback.message.edit_text(text=texts.Help.REDIRECTION)
#     await callback.answer()
#
#
# @postponed(Dispatcher.callback_query_handler, text='Help Support', state=[None, Registration.Onboarding])
# async def support_handler(callback: types.CallbackQuery, state: FSMContext):
#     await callback.message.edit_text(text=texts.Help.SUPPORT)
#     await callback.answer()
#
#
# @postponed(Dispatcher.callback_query_handler, text='Help Tariff', state=[None, Registration.Onboarding])
# async def tariff_handler(callback: types.CallbackQuery, state: FSMContext):
#     await callback.message.edit_text(text=texts.Help.TARIFF)
#     await callback.answer()
#
#
# @postponed(Dispatcher.callback_query_handler, text='Help Disable Robot', state=[None, Registration.Onboarding])
# async def disable_handler(callback: types.CallbackQuery, state: FSMContext):
#     await callback.message.edit_text(text=texts.Help.DISABLE_ROBOT)
#     await callback.answer()


@postponed(Dispatcher.message_handler, state=Registration.Onboarding)
async def error_choose_tariff_command(message: types.Message):
    if message.text[0] == '/':
        await answer_message(message, "Пока вы не можете пользоваться этой командой\nВыберите тариф")
    await message.delete()


@postponed(Dispatcher.callback_query_handler, text='Tariff Very Busy', state=[None, Registration.Onboarding])
async def tariff_handler(callback: types.CallbackQuery, state: FSMContext):
    await tariff_handler_common(callback, state, common.Plans.VERY_BUSY)


@postponed(Dispatcher.callback_query_handler, text='Tariff Super Busy', state=[None, Registration.Onboarding])
async def tariff_handler(callback: types.CallbackQuery, state: FSMContext):
    await tariff_handler_common(callback, state, common.Plans.SUPER_BUSY)


@postponed(Dispatcher.callback_query_handler, text='Tariff Ultra Busy', state=[None, Registration.Onboarding])
async def tariff_handler(callback: types.CallbackQuery, state: FSMContext):
    await tariff_handler_common(callback, state, common.Plans.ULTRA_BUSY)


async def tariff_handler_common(callback: types.CallbackQuery, state: FSMContext, plan_id: common.Plans):
    # # TODO: better way to change main kb along with integrated?
    # dummy_msg = await answer_message(callback.message, text="<i>Загрузка...</i>",
    #                                  reply_markup=aiogram.types.ReplyKeyboardRemove())

    async with state.proxy() as data:
        data["onboarding"] = await state.get_state() == "Registration:Onboarding"

    async with db.DatabaseApi().session():
        user: db.User = await get_user(callback)
        payment_token = user.payment_token
        payment_method_string = user.payment_method_string
        plan_name = common.Plans.get_name(plan_id)

        is_free_trial: bool = False

        if UserFreeTrialUtil(user).can_use() and plan_id == common.Plans.VERY_BUSY:
            logging.info(f"Providing free trial to {user.id} ({user.get_pretty_name()})")
            is_free_trial = True

        try:
            amo_contact: db.AmoContact = await db.DatabaseApi().get_amo_contact(user_id=user.id)
            amo_lead: db.AmoLead = await db.DatabaseApi().get_amo_lead(contact_id=amo_contact.id)
            amo_lead.status_id = amoCRM.entities.lead_buying_tariff_id

            await amoCRM.client.update_leads(
                amoCRM.entities.get_updating_lead_object(
                    lead_id=amo_lead.id,
                    status_id=amoCRM.entities.lead_buying_tariff_id
                    )
                )
        except Exception:
            logging.warning("AmoCRM error", exc_info=True)

        if user.subscription_id == plan_id:
            kb = kb_main_with_number() if user.given_phone != "" else kb_main_without_number()
            await answer_message(
                callback.message,
                text=f"У вас уже подключен {plan_name}\n"
                     f"Если вы хотите сменить тариф, выберите другой",
                reply_markup=kb
                )
            # await dummy_msg.delete()
            return

        if payment_token is None:
            payment_method_text = texts.Payment.PAYMENT_GATEWAY
            if is_free_trial:
                payment_method_text += "\n(Это необходимо для защиты от мошенничества. Сумма перевода будет автоматически возвращена вам)"
            kb = ikb_tariff_change_confirmation()
        else:
            payment_method_text = f"Метод оплаты: {payment_method_string}\n"
            kb = ikb_tariff_change_confirmation_paymethod()

        async with state.proxy() as data:
            data["plan_id"] = plan_id
            data["is_free_trial"] = is_free_trial
            free_trial_text: str = "Бесплатный пробный период: 2 недели\n" if is_free_trial else ""
            await answer_message(
                callback.message,
                text=f"Вы уверены, что хотите приобрести тариф {common.Plans.get_name(plan_id)}?\n"
                     f"{free_trial_text}"
                     f"{payment_method_text}",
                reply_markup=kb
                )
            await TariffPurchase.ChooseTariffConfirmation.set()
            await callback.message.delete()

    # await dummy_msg.delete()


@postponed(
    Dispatcher.callback_query_handler, text='Confirm', state=[TariffPurchase.ChooseTariffConfirmation,
                                                              TariffPurchase.UnsuccessfulPayment]
    )
@postponed(
    Dispatcher.callback_query_handler, text='Change payment method', state=[TariffPurchase.ChooseTariffConfirmation,
                                                                            TariffPurchase.UnsuccessfulPayment]
    )
async def tariff_handler_confirm(callback: types.CallbackQuery, state: FSMContext):
    async with db.DatabaseApi().session():
        async with state.proxy() as data:
            plan_id = data["plan_id"]
            plan_name = common.Plans.get_name(plan_id)

            is_free_trial = data["is_free_trial"]

            user: db.User = await get_user(callback)
            plan: db.Plan = await get_plan_by_id(plan_id)

            if user.payment_token is None or callback.data == "Change payment method":
                order: cp_methods.Order
                payment_text: str
                if is_free_trial:
                    order = await cp_methods.create_order(
                        user, plan, cp_types.PaymentReasons.FREE_TRIAL_VERIFICATION_PAYMENT, price_override=1
                        )
                    payment_text = (
                        f"Чтобы продолжить, вам необходимо совершить подтверждающий платёж (1 рубль). Платёж будет возвращён автоматически\n"
                        f"Ваша ссылка для оплаты: {order.url}"
                    )
                else:
                    order = await cp_methods.create_order(user, plan, cp_types.PaymentReasons.REGULAR_PLAN_SUBSCRIPTION)
                    payment_text = (
                        f"Ваша ссылка для оплаты: {order.url}"
                    )
                # await session.commit()

                await answer_message(
                    callback.message,
                    text=payment_text,
                    reply_markup=ikb_cancel_payment()
                    )

                if "onboarding" in data and data["onboarding"]:
                    await Registration.Onboarding.set()
                else:
                    await state.finish()
            else:
                try:
                    transaction_id: int | None = None
                    if not is_free_trial:
                        tx = await cp_methods.charge(user, plan, cp_types.PaymentReasons.REGULAR_PLAN_SUBSCRIPTION)
                        transaction_id = tx.transaction_id
                        del tx

                    virt_number: str = await change_subscription(
                        callback, plan_id, transaction_id, free_trial=is_free_trial
                        )

                    logging.info(f"virtual number is {virt_number}")

                    kb = kb_main_with_number() if virt_number != "" else kb_main_without_number()
                    await answer_message(
                        callback.message, text=f"Вы успешно приобрели тариф \"{plan_name}\"",
                        reply_markup=kb
                        )

                    if virt_number != "":
                        virt_number_msg: types.Message = await answer_message(
                            callback.message,
                            text=f"Ваш второй номер: {virt_number}"
                            )
                        await virt_number_msg.pin()
                    else:
                        await bot.unpin_all_chat_messages(chat_id=callback.from_user.id)

                    if "onboarding" in data and data["onboarding"]:
                        await answer_message(
                            callback.message, text=texts.Tariff.REDIRECTION,
                            reply_markup=kb
                            )

                    await state.finish()
                    await callback.answer()

                except cp_types.CpPaymentError:
                    logging.warning("CpPaymentError:", exc_info=True)
                    await answer_message(
                        callback.message,
                        text=texts.Payment.ERROR_PAYMENT,
                        reply_markup=ikb_tariff_change_payment_failed()
                        )
                    await TariffPurchase.UnsuccessfulPayment.set()

            del data["is_free_trial"]

    await callback.message.delete()


async def successful_subscription(telegram_id: str, plan_id: int, virt_number: str):
    state = FSMContext(storage=dp.storage, chat=telegram_id, user=telegram_id)
    plan_name = common.Plans.get_name(plan_id)

    try:
        import config
        if config.BRANCH == 'master':
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url=f'https://test.busy.contact/logs/users/subscribe?apiKey={config.API_KEY}',
                    headers={"Content-Type": "application/json; charset=utf-8"},
                    data=json.dumps({'username': f'Пользователь {telegram_id}'})
                ) as r:
                    if r.status != 200:
                        logging.error("unsuccessful post-request about subscription")
    except Exception:
        logging.exception('error while notifying about subscription')

    kb = kb_main_with_number() if virt_number != "" else kb_main_without_number()
    await send_message(
        chat_id=telegram_id, text=f"Вы успешно приобрели тариф \"{plan_name}\"",
        reply_markup=kb
        )

    if virt_number != "":
        virt_number_msg: types.Message = await send_message(
            chat_id=telegram_id,
            text=f"Ваш второй номер: {virt_number}"
            )
        await bot.pin_chat_message(chat_id=telegram_id, message_id=virt_number_msg.message_id)

    async with state.proxy() as data:
        if "onboarding" in data and data["onboarding"]:
            await send_message(chat_id=telegram_id, text=texts.Tariff.REDIRECTION, reply_markup=kb)

    async with db.DatabaseApi().session():
        try:
            user = await db.DatabaseApi().find_user(telegram_id=telegram_id)
            amo_contact: db.AmoContact = await db.DatabaseApi().get_amo_contact(user_id=user.id)
            amo_lead: db.AmoLead = await db.DatabaseApi().get_amo_lead(contact_id=amo_contact.id)
            amo_lead.status_id = amoCRM.entities.lead_success_closed_id
            await amoCRM.client.update_leads(
                amoCRM.entities.get_updating_lead_object
                (lead_id=amo_lead.id, status_id=amoCRM.entities.lead_success_closed_id)
                )
        except Exception:
            logging.warning("AmoCRM error", exc_info=True)

    await state.finish()


@postponed(Dispatcher.callback_query_handler, text='Back', state=[TariffPurchase.ChooseTariffConfirmation])
async def tariff_handler_back(callback: types.CallbackQuery, state: FSMContext):
    # async with db.DatabaseApi().session():
    #     user: db.User = await get_user(callback)
    #     kb = kb_main_with_number() if user.given_phone != "" else kb_main_without_number()
    #     kb = kb if user.subscription is not None else kb_welcome()

    # # TODO: better way to change main kb along with integrated?
    # dummy_msg = await answer_message(callback.message, text="<i>Загрузка...</i>", reply_markup=kb)

    await answer_message(callback.message, text=texts.Tariff.TARIFF_MESSAGE, reply_markup=ikb_tariff())
    async with state.proxy() as data:
        onboarding = data["onboarding"]

    if onboarding:
        await Registration.Onboarding.set()
    else:
        await state.finish()

    await callback.message.delete()
    # TODO: how to delete it without loosing the keyboard?
    # await dummy_msg.delete()


@postponed(
    Dispatcher.callback_query_handler, text='CancelPayment',
    state=[None, Registration.Onboarding, TariffPurchase.UnsuccessfulPayment]
    )
async def tariff_handler_cancel_payment(callback: types.CallbackQuery, state: FSMContext):
    async with db.DatabaseApi().session():
        user: db.User = await get_user(callback)
        await cp_methods.cancel_order(user)

        # kb = kb_main_with_number() if user.given_phone != "" else kb_main_without_number()
        # kb = kb if user.subscription is not None else kb_welcome()

    await answer_message(callback.message, text=texts.Payment.CANCEL_PAYMENT)
    await callback.message.delete()


async def successful_payment(telegram_id: str, plan_id: int, plan_price: int):
    plan_name = common.Plans.get_name(plan_id)
    await send_message(
        chat_id=telegram_id,
        text=f"Уведомляем о том, что была списана плата за тариф \"{plan_name}\" "
             f"в размере {plan_price} рублей"
        )


async def unsuccessful_payment(telegram_id: str, plan_id: int, plan_price: int, *, is_extra: bool):
    plan_name = common.Plans.get_name(plan_id)

    try:
        await send_message(
            chat_id=telegram_id,
            text=f"Уведомляем о том, что не смогли списать плату за тариф \"{plan_name}\" "
                 f"в размере {plan_price} рублей.\n"
                 f"Убедитесь, что привязанная карта действительна и на ней достаточно средств.\n",
            reply_markup=ikb_extra_fail() if is_extra else ikb_recurrent_fail()
            )
    except Exception:
        logging.warning('blocked by user')


@postponed(Dispatcher.callback_query_handler, text='Failed Recurrent Retry', state=None)
async def failed_recurrent_retry(callback: types.CallbackQuery, state: FSMContext):
    async with db.DatabaseApi().session():
        user: db.User = await get_user(callback)
        plan: db.Plan = user.subscription

        plan_name = common.Plans.get_name(plan.id)
        plan_price = plan.price

        assert common.ExtraData.FAILED_RECURRENT_RECOVERED in user.extra_data
        if user.extra_data[common.ExtraData.FAILED_RECURRENT_RECOVERED]:
            await answer_message(callback.message, text=texts.Payment.ALREADY_PAYED)
            await callback.message.delete()
            return

        try:
            tx = await cp_methods.charge(user, plan, cp_types.PaymentReasons.REGULAR_PLAN_MANUAL_RETRY)
            await common.renew_subscription(user, tx.transaction_id)

            # await session.commit()

            await answer_message(
                callback.message,
                text=f"Списано {plan_price} рублей за тариф {plan_name}.\n"
                     f"Вы можете продолжать пользоваться сервисом.\n"
                     f"Извините за неудобства"
                )

            user.extra_data = user.extra_data | {common.ExtraData.FAILED_RECURRENT_RECOVERED: True}

        except cp_types.CpPaymentError:
            await answer_message(
                callback.message,
                text=f"Уведомляем о том, что не смогли списать плату за тариф \"{plan_name}\" "
                     f"в размере {plan_price} рублей.\n"
                     f"Убедитесь, что привязанная карта действительна и на ней достаточно средств.\n",
                reply_markup=ikb_recurrent_fail()
                )

    await callback.message.delete()


@postponed(Dispatcher.callback_query_handler, text='Failed Extra Retry', state=None)
async def failed_extra_retry(callback: types.CallbackQuery, state: FSMContext):
    async with db.DatabaseApi().session():
        user: db.User = await get_user(callback)
        plan: db.Plan = await get_plan_by_id(common.Plans.EXTRA)

        plan_name = common.Plans.get_name(plan.id)
        plan_price = plan.price

        assert common.ExtraData.FAILED_EXTRA_RECOVERED in user.extra_data
        if user.extra_data[common.ExtraData.FAILED_EXTRA_RECOVERED]:
            await answer_message(callback.message, text=f"Вы уже заплатили :)")
            await callback.message.delete()
            return

        try:
            tx = await cp_methods.charge(user, plan, cp_types.PaymentReasons.EXTRA_PLAN_MANUAL_RETRY)
            await common.activate_extra_plan(user, plan, payment_id=tx.transaction_id)

            # await session.commit()

            await answer_message(
                callback.message,
                text=f"Списано {plan_price} рублей за тариф {plan_name}.\n"
                     f"Вы можете продолжать пользоваться сервисом.\n"
                     f"Извините за неудобства"
                )

            user.extra_data = user.extra_data | {common.ExtraData.FAILED_EXTRA_RECOVERED: True}

        except cp_types.CpPaymentError:
            await answer_message(
                callback.message,
                text=f"Уведомляем о том, что не смогли списать плату за тариф \"{plan_name}\" "
                     f"в размере {plan_price} рублей.\n"
                     f"Убедитесь, что привязанная карта действительна и на ней достаточно средств.\n",
                reply_markup=ikb_extra_fail()
                )

    await callback.message.delete()


@postponed(Dispatcher.callback_query_handler, text='Recurrent change payment method', state=None)
async def failed_recurrent_paymethod(callback: types.CallbackQuery, state: FSMContext):
    async with state.proxy(), db.DatabaseApi().session():
        user: db.User = await get_user(callback)
        plan: db.Plan = user.subscription

        assert common.ExtraData.FAILED_RECURRENT_RECOVERED in user.extra_data
        if user.extra_data[common.ExtraData.FAILED_RECURRENT_RECOVERED]:
            await answer_message(callback.message, text=f"Вы уже заплатили :)")
            await callback.message.delete()
            return

        order = await cp_methods.create_order(user, plan, cp_types.PaymentReasons.REGULAR_PLAN_MANUAL_RETRY)
        # await session.commit()

        await answer_message(
            callback.message,
            text=f"Ваша ссылка: {order.url}\n"
                 f"Для продолжение совершите оплату",
            reply_markup=ikb_cancel_payment()
            )

    await callback.message.delete()


@postponed(Dispatcher.callback_query_handler, text='Extra change payment method', state=None)
async def failed_extra_paymethod(callback: types.CallbackQuery, state: FSMContext):
    async with state.proxy(), db.DatabaseApi().session():
        user: db.User = await get_user(callback)
        plan: db.Plan = await get_plan_by_id(common.Plans.EXTRA)

        assert common.ExtraData.FAILED_EXTRA_RECOVERED in user.extra_data
        if user.extra_data[common.ExtraData.FAILED_EXTRA_RECOVERED]:
            await answer_message(callback.message, text=texts.Payment.ALREADY_PAYED)
            await callback.message.delete()
            return

        order = await cp_methods.create_order(user, plan, cp_types.PaymentReasons.EXTRA_PLAN_MANUAL_RETRY)
        # await session.commit()

        await answer_message(
            callback.message,
            text=f"Ваша ссылка: {order.url}\n"
                 f"Для продолжение совершите оплату",
            reply_markup=ikb_cancel_payment()
            )

    await callback.message.delete()


async def successful_payment_retry(telegram_id: str, plan_id: int, plan_price: int, payment_method_string: str):
    plan_name = common.Plans.get_name(plan_id)
    await send_message(
        chat_id=telegram_id,
        text=f"Списано {plan_price} рублей за тариф {plan_name}.\n"
             f"Вы можете продолжать пользоваться сервисом.\n"
             f"Новый метод оплаты {payment_method_string} сохранен "
             f"и будет использоваться нами в дальнейшем.\n"
             f"Извините за неудобства."
        )


async def user_kicked(telegram_id: str, plan_id: int, has_number: bool):
    state = FSMContext(storage=dp.storage, chat=telegram_id, user=telegram_id)
    plan_name = common.Plans.get_name(plan_id)

    if has_number:
        text = f"Уведомляем о том, что ваша подписка {plan_name} была отменена за бездействие.\n" \
               f"Также, ваш виртуальный номер был деактивирован.\n"
    else:
        text = f"Уведомляем о том, что ваша подписка {plan_name} была отменена за бездействие.\n"

    await send_message(chat_id=telegram_id, text=text, reply_markup=kb_welcome())
    await state.set_state(Registration.Onboarding.state)


async def user_unsubscribed_ext(telegram_id: str):
    state = FSMContext(storage=dp.storage, chat=telegram_id, user=telegram_id)

    await send_message(chat_id=telegram_id, text=texts.LEAVE_MESSAGE, reply_markup=kb_welcome())
    await bot.unpin_all_chat_messages(chat_id=telegram_id)
    await state.set_state(Registration.Onboarding.state)


@postponed(Dispatcher.message_handler, Text(equals="Позвонить 📞"))
@postponed(Dispatcher.message_handler, commands=['call'])
async def outgoing_call_command(message: types.Message, state: FSMContext):
    async with db.DatabaseApi().session():
        has_virtual_number, has_service = await check_outgoing(message, need_calls=True)

        if not has_virtual_number:
            await answer_message(message, text=texts.CallChangeTariff.CALL)
            return
        elif not has_service:
            await answer_message(message, text=texts.NO_AVAILABLE_CALLS)
            return

    await ProfileCall.GetCallNumber.set()
    await message.delete()
    await answer_message(message, text=texts.OUTBOUND_CALL_ADDRESS, reply_markup=kb_cancel())


@postponed(
    Dispatcher.message_handler, Text(equals='Отмена ◀️'), state=[ProfileCall.GetCallNumber, ProfileCall.GetConfirm]
    )
async def cancel_handler(message: types.Message, state: FSMContext):
    await answer_message(message, text="Отменено", reply_markup=kb_main_with_number())
    await message.delete()
    await state.finish()


@postponed(Dispatcher.message_handler, content_types=types.ContentType.CONTACT, state=ProfileCall.GetCallNumber)
async def get_number(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        number = strip_number(message.contact.phone_number)
        data['number'] = number
        await answer_message(
            message,
            text=f"Вы хотите позвонить на номер: {prettify_number(number)}?",
            reply_markup=ikb_confirm()
            )
        await message.delete()
        await ProfileCall.next()


@postponed(Dispatcher.message_handler, state=ProfileCall.GetCallNumber)
async def get_number(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        number = strip_number(message.text)
        data['number'] = number
        if number.isdigit() and len(number) == 11:
            await answer_message(
                message, text=f"Вы хотите позвонить на номер: {prettify_number(number)}?",
                reply_markup=ikb_confirm()
                )
            await message.delete()
            await ProfileCall.next()
        else:
            await message.delete()
            await answer_message(message, text="Введите корректный номер")


async def start_outbound_call(callback: types.CallbackQuery, destination: str) -> None:
    async with db.DatabaseApi().session(allow_reuse=True):
        user = await db.DatabaseApi().find_user(telegram_id=callback.from_user.id)
        given_phone = user.given_phone
        own_phone = user.own_phone
        user_id = user.id

        if (given_phone or own_phone) is None:
            logging.warning(f'Given phone or own_phone are not found to outbound call to {destination}')
            return

        if not (await common.bill(user, charge_call=True)):
            await answer_message(callback.message, text=texts.NO_AVAILABLE_CALLS, reply_markup=kb_main_with_number())
            return

        call_id = uuid.uuid4()
        outbound_call_data = await voximplant.client.start_outbound_call(
            caller=own_phone,
            destination=destination,
            voximplant_number=given_phone,
            call_id=str(call_id)
            )

        outbound_call_result = outbound_call_data.get('result')
        if outbound_call_result != 1:
            logging.error(f'Unsuccessful outbound call to {destination}')
            await answer_message(callback.message, text='Не получилось дозвониться')
            return

        session_id = outbound_call_data.get('call_session_history_id')
        if session_id is None:
            logging.warning(f'Wrong call session id of outbound call to {destination}')
            return

        message = await answer_message(callback.message, text=texts.OUTBOUND_CALL_START)
        message_id = message.message_id

    async with db.DatabaseApi().session() as session:
        call_object = await db.DatabaseApi().get_call_object(call_id=call_id)
        if call_object is None:
            call = db.model.Call(
                uid=call_id,
                user_id=user_id,
                callee_number=destination,
                caller_number=given_phone,
                session_id=str(session_id),
                tg_message_id=message_id,
                timestamp=datetime.now()
                )
            session.add(call)
        else:
            call_object.session_id = str(session_id)
            call_object.tg_message_id = message_id


async def finish_outbound_call(call_id: uuid, telegram_id: str, commands: list, record: str):
    state = FSMContext(storage=dp.storage, chat=telegram_id, user=telegram_id)
    cur_state = await state.get_state()
    logging.info(f"State after outbound is {cur_state}")
    while True:
        if str(await state.get_state()) in ['ProfileIncomingCall:LineIsBusy', 'ProfileCall:GetCallNumber',
                                            'ProfileCall:GetConfirm', 'ProfileSendMessage:GetMessageNumber',
                                            'ProfileSendMessage:GetConfirmNumber', 'ProfileSendMessage:GetMessage',
                                            'ProfileSendMessage:GetConfirmMessage']:
            logging.info("Other state now")
            await asyncio.sleep(5)
        else:
            break
    async with db.DatabaseApi().session():
        call_object = await db.DatabaseApi().get_call_object(call_id=call_id)
        number = call_object.callee_number
        text_message: str
        text_message = f'Запись разговора с {prettify_number(number)}:\n'
        for command in commands:
            if command['command_name'] == 'message':
                if command['contents']['side'] == 'user':
                    if command['contents']['type'] == 'part':
                        text_message += '😎: ' + command['contents']['text'] + '...' + '\n'
                    elif command['contents']['type'] == 'whole':
                        text_message += '😎: ' + command['contents']['text'] + '\n'
                elif command['contents']['side'] == 'callee':
                    if command['contents']['type'] == 'part':
                        text_message += '👨: ' + command['contents']['text'] + '...' + '\n'
                    elif command['contents']['type'] == 'whole':
                        text_message += '👨: ' + command['contents']['text'] + '\n'

        # message_id = call_object.tg_message_id
        # await bot.delete_message(chat_id=telegram_id, message_id=message_id)
        await send_message(chat_id=telegram_id, text=text_message, reply_markup=ikb_call_back())
        await send_audio_dialog(telegram_id, record, number)

    await state.finish()


async def send_audio_dialog(telegram_id: str, url: str, number: str):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            voice = await response.read()
            async with aiofiles.open(f'voice_{telegram_id}-{number}.mp3', 'wb+') as f:
                await f.write(voice)
                voice = AudioSegment.from_mp3(f.name).export(
                    f'voice_{telegram_id}-{number}.ogg',
                    format='ogg', codec="libopus"
                    )
                await bot.send_audio(
                    chat_id=telegram_id, audio=voice, caption=f'Запись звонка. Номер собеседника: '
                                                              f'{number}'
                    )
            os.remove(f'voice_{telegram_id}-{number}.mp3')
            os.remove(f'voice_{telegram_id}-{number}.ogg')


@postponed(Dispatcher.callback_query_handler, text='Yes', state=ProfileCall.GetConfirm)
async def yes_handler(callback: types.CallbackQuery, state: FSMContext):
    async with state.proxy() as data:
        await callback.message.delete()
        # Note: shouldn't actually need to strip this again, but why not
        number_to_call: str = strip_number(data['number'])

        await answer_message(
            callback.message, text=f'Отлично, звоним на номер {prettify_number(number_to_call)}',
            reply_markup=kb_main_with_number()
            )

        await start_outbound_call(callback=callback, destination=data['number'])

        await state.finish()
        await callback.answer()


@postponed(Dispatcher.callback_query_handler, text='Change', state=ProfileCall.GetConfirm)
async def change_handler(callback: types.CallbackQuery):
    await answer_message(callback.message, text='Введите номер еще раз')
    await callback.message.delete()
    await ProfileCall.GetCallNumber.set()
    await callback.answer()


@postponed(Dispatcher.message_handler, state=ProfileCall.GetConfirm)
async def get_confirm_handler(message: types.Message):
    if message.text[0] == '/':
        await answer_message(message, "Пока вы не можете пользоваться командами")
    await message.delete()


@postponed(Dispatcher.message_handler, commands=['sms'])
@postponed(Dispatcher.message_handler, Text(equals="Отправить смс 📩"))
async def outgoing_call_command(message: types.Message):
    async with db.DatabaseApi().session():
        has_virtual_number, has_service = await check_outgoing(message, need_messages=True)

    if not has_virtual_number:
        await answer_message(message, text=texts.CallChangeTariff.SMS)
    elif not has_service:
        await answer_message(message, text=texts.NO_AVAILABLE_SMS)
    else:
        await ProfileSendMessage.GetMessageNumber.set()
        await message.delete()
        await answer_message(message, text=texts.SMS_ADDRESS, reply_markup=kb_cancel())


@postponed(Dispatcher.message_handler, Text(equals='Отмена ◀️'), state=ProfileSendMessage.all_states)
async def cancel_handler(message: types.Message, state: FSMContext):
    await answer_message(message, text="Отменено", reply_markup=kb_main_with_number())
    await message.delete()
    await state.finish()


@postponed(
    Dispatcher.message_handler, content_types=types.ContentType.CONTACT, state=ProfileSendMessage.GetMessageNumber
    )
async def get_number(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        number = strip_number(message.contact.phone_number)
        data['number'] = number

        await answer_message(
            message, text=f"Вы хотите отправить сообщение на номер: {prettify_number(number)}?",
            reply_markup=ikb_confirm()
            )
        await message.delete()
        await ProfileSendMessage.next()


@postponed(Dispatcher.message_handler, state=ProfileSendMessage.GetMessageNumber)
async def get_number(message: types.Message, state: FSMContext):
    async with state.proxy() as data:
        number = strip_number(message.text)
        data['number'] = number

        if number.isdigit() and len(number) == 11:
            await answer_message(
                message, text=f"Вы хотите отправить сообщение на номер: {prettify_number(number)}?",
                reply_markup=ikb_confirm()
                )
            await message.delete()
            await ProfileSendMessage.next()
        else:
            await message.delete()
            await answer_message(message, text="Введите корректный номер")


@postponed(Dispatcher.callback_query_handler, text='Yes', state=ProfileSendMessage.GetConfirmNumber)
async def yes_handler(callback: types.CallbackQuery, state: FSMContext):
    async with state.proxy() as data:
        edited_msg = await callback.message.edit_text(
            text=f"Сообщение будет отправлено на номер: {data['number']}\n"
                 f"Введите текст сообщения"
            )
        await record_message(edited_msg, callback.message)
        await ProfileSendMessage.next()
        await callback.answer()


@postponed(Dispatcher.callback_query_handler, text='Change', state=ProfileSendMessage.GetConfirmNumber)
async def change_handler(callback: types.CallbackQuery):
    await answer_message(callback.message, text='Введите номер еще раз')
    await callback.message.delete()
    await ProfileSendMessage.GetMessageNumber.set()
    await callback.answer()


@postponed(Dispatcher.message_handler, state=ProfileSendMessage.GetMessage)
async def get_number(message: types.Message, state: FSMContext):
    await message.delete()
    await ProfileSendMessage.next()
    async with state.proxy() as data:
        data['message'] = message.text
        await answer_message(message, text='Ваше сообщение:\n' + data['message'], reply_markup=ikb_confirm())


@postponed(Dispatcher.callback_query_handler, text='Yes', state=ProfileSendMessage.GetConfirmMessage)
async def yes_handler(callback: types.CallbackQuery, state: FSMContext):
    async with state.proxy() as data:
        async with db.DatabaseApi().session():
            user = await db.DatabaseApi().find_user(telegram_id=callback.from_user.id)
            given_phone = user.given_phone

            billed = await common.bill(user, charge_msg=True)
            user_id = user.id

        if billed:
            await voximplant.client.send_sms_message(
                source=given_phone,
                destination=data['number'],
                sms_body=data['message']
                )

            await callback.message.delete()
            await answer_message(
                callback.message, text=f"Сообщение отправлено на номер: {data['number']}.\n"
                                       f"Текст сообщения: {data['message']}",
                reply_markup=kb_main_with_number()
                )
        else:
            await callback.message.delete()
            await answer_message(callback.message, text=texts.NO_AVAILABLE_SMS, reply_markup=kb_main_with_number())

        await common.handle_advance_service(user_id, charge_msg=True)

        await ProfileSendMessage.next()
        await callback.answer()


@postponed(Dispatcher.callback_query_handler, text='Change', state=ProfileSendMessage.GetConfirmMessage)
async def change_handler(callback: types.CallbackQuery):
    await answer_message(callback.message, text='Введите текст сообщения еще раз')
    await callback.message.delete()
    await ProfileSendMessage.GetMessage.set()
    await callback.answer()


@postponed(
    Dispatcher.message_handler, state=[ProfileSendMessage.GetConfirmNumber, ProfileSendMessage.GetConfirmMessage]
    )
async def get_confirm_handler(message: types.Message):
    if message.text[0] == '/':
        await answer_message(message, "Пока вы не можете пользоваться командами")
    await message.delete()


@postponed(Dispatcher.message_handler, Text(equals='Мой тариф 💵'))
@postponed(Dispatcher.message_handler, commands=['tariff'])
async def my_tariff_command(message: types.Message):
    async with db.DatabaseApi().session():
        user: db.User = await get_user(message)
        assert user.subscription is not None, "Someone called tariff command without actual subscription"

        active_plans = await common.get_active_plans(user=user)
        text_message = ''

        if len(active_plans) > 0:
            for active_plan in active_plans:
                active_plan_end = str(active_plan.end)[0:10]
                plan_name = common.Plans.get_name(active_plan.plan_id)
                plan = await db.DatabaseApi().get_plan(plan_id=active_plan.plan_id)

                sms_string = f"Остаток смс в этом месяце {active_plan.messages_left}/{plan.messages}\n" \
                    if user.given_phone != "" else ""

                text_message += f"Текущий тариф <b>{plan_name}</b>\n" \
                                f"Остаток звонков в этом месяце: {active_plan.calls_left}/{plan.calls}\n" \
                                f"{sms_string}" \
                                f"Действует до {active_plan_end}\n\n"
        else:
            plan_name = common.Plans.get_name(user.subscription.id)
            text_message += f"Текущий тариф <b>{plan_name}</b>\n" \
                            f"Нет действующих пакетов\n"

        await answer_message(message, text=text_message, reply_markup=ikb_my_tariff())


@postponed(Dispatcher.message_handler, Text(equals="Настройки ⚙️"))
@postponed(Dispatcher.message_handler, commands=['settings'])
async def setting_command(message: types.Message):
    async with db.DatabaseApi().session():
        user: db.User = await get_user(message)
        user_config = await common.get_user_config(user)

        kb = ikb_setting(
            with_number=bool(user.given_phone),
            extra_autocharge=user.extra_plan_autocharge
        )

        await answer_message(message, text="Настройки ⚙️", reply_markup=kb)
    await message.delete()


@postponed(Dispatcher.callback_query_handler, text='Change tariff')
async def change_tariff_handler(callback: types.CallbackQuery, state: FSMContext):
    async with db.DatabaseApi().session():
        user: db.User = await db.DatabaseApi().find_user(telegram_id=callback.from_user.id)
        assert user.subscription is not None, "Someone called tariff command without actual subscription"

        active_plans = await common.get_active_plans(user=user)
        text_message = ''
        if len(active_plans) > 0:
            for active_plan in active_plans:
                active_plan_end = str(active_plan.end)[0:10]
                plan_name = common.Plans.get_name(active_plan.plan_id)
                plan = await db.DatabaseApi().get_plan(plan_id=active_plan.plan_id)

                sms_string = f"Остаток смс в этом месяце {active_plan.messages_left}/{plan.messages}\n" \
                    if user.given_phone != "" else ""

                text_message += f"Текущий тариф <b>{plan_name}</b>\n" \
                                f"Остаток звонков в этом месяце: {active_plan.calls_left}/{plan.calls}\n" \
                                f"{sms_string}" \
                                f"Действует до {active_plan_end}\n\n"
        else:
            plan_name = common.Plans.get_name(user.subscription.id)
            text_message += f"Текущий тариф <b>{plan_name}</b>\n" \
                            f"Нет действующих пакетов\n"

    edited_msg = await callback.message.edit_text(
        text=text_message + "\n\nВыберите тариф для смены:\n\n" +
             texts.Tariff.TARIFF_MESSAGE +
             "\n\nЧтобы сменить тариф, нажмите на кнопку\n\nПри смене тарифа, "
             "текущие остатки пакета не будут перенесены",
        reply_markup=ikb_tariff()
        )
    await record_message(edited_msg, callback.message)
    await callback.answer()


@postponed(Dispatcher.callback_query_handler, text='Connect a virtual number')
async def connect_virtual_number_handler(callback: types.CallbackQuery):
    await answer_message(callback.message, text=texts.CallChangeTariff.CALL)
    await callback.answer()


@postponed(Dispatcher.callback_query_handler, text='Toggle autocharge')
async def toggle_autocharge_handler(callback: types.CallbackQuery):
    async with db.DatabaseApi().session():
        user: db.User = await get_user(callback)
        user.extra_plan_autocharge = not user.extra_plan_autocharge

        if user.extra_plan_autocharge:
            await answer_message(callback.message, text=texts.EXTRA_AUTO_CHARGE_ON)
        else:
            await answer_message(callback.message, text=texts.EXTRA_AUTO_CHARGE_OFF)

    await callback.message.delete()
    await callback.answer()


@postponed(Dispatcher.callback_query_handler, text='Customize')
async def customize_handler(callback: types.CallbackQuery):
    async with db.DatabaseApi().session():
        user: db.User = await get_user(callback)
        user_config = await common.get_user_config(user)

        kb = ikb_customize(user_config)

    await Customization.Menu.set()
    sent_message: types.Message = await answer_message(callback.message, text="<i>Загрузка...</i>", reply_markup=ReplyKeyboardRemove())
    await bot.delete_message(chat_id=sent_message.chat.id, message_id=sent_message.message_id)
    await answer_message(callback.message, text=texts.Customize.ROOT, reply_markup=kb)
    await callback.answer()


@postponed(Dispatcher.callback_query_handler, state=Customization.Menu, text="Back")
async def customize_back_handler(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.delete()  # TODO: ?
    await state.finish()
    async with db.DatabaseApi().session():
        user: db.User = await get_user(callback)
        user_config = await common.get_user_config(user)

        kb = ikb_setting(
            with_number=bool(user.given_phone),
            extra_autocharge=user.extra_plan_autocharge
        )
        await answer_message(callback.message, text="<i>Загрузка...</i>", reply_markup=kb_main(with_number=bool(user.given_phone)))
        await answer_message(callback.message, text="Настройки ⚙️", reply_markup=kb)


@postponed(Dispatcher.callback_query_handler, state=[Customization.Greeting, Customization.Name, Customization.ChatGPTInstructions], text="Cancel")
async def customize_cancel_handler(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.delete()
    await state.finish()
    await Customization.Menu.set()

@postponed(Dispatcher.callback_query_handler, state=Customization.Menu, text="Cancel")
async def customize_cancel_handler(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.delete()
    async with db.DatabaseApi().session():
        user: db.User = await get_user(callback)
        user_config = await common.get_user_config(user)
    await answer_message(
        callback.message, text=texts.Customize.ROOT,
        reply_markup=ikb_customize(user_config)
        )

@postponed(Dispatcher.callback_query_handler, state=Customization.IgnorelistAdd, text="Cancel")
async def customize_cancel_handler(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.delete()
    await state.finish()
    await Customization.Ignorelist.set()
    await callback.answer()


@postponed(Dispatcher.callback_query_handler, state=Customization.Menu, text='Change greeting')
async def customize_greeting_handler(callback: types.CallbackQuery):
    await Customization.Greeting.set()
    kb = ikb_cancel_customization()
    await answer_message(callback.message, text=texts.Customize.GREETING, reply_markup=kb)
    await callback.answer()


def is_valid_russian_text(text: str) -> bool:
    count = 0
    words = text.split()
    cleaned_words =  list(filter(None, [word.strip(",.:;!?") for word in words]))
    threshold = 0.5

    for word in cleaned_words:
        if not contains_only_russian_letters(word):
            return False
        parse = MorphAnalyzer().parse(word)
        if parse[0].score > threshold:
            count += 1
    return count > len(cleaned_words) // 2


@postponed(Dispatcher.message_handler, state=Customization.Greeting, content_types=types.ContentTypes.TEXT)
async def customize_greeting_handler(message: types.Message, state: FSMContext):
    async with db.DatabaseApi().session():
        user: db.User = await get_user(message)
        user_config = await common.get_user_config(user)
    
    value: str = message.text

    if len(value) > 128:
        await answer_message(
            message,
            text="Сообщение приветствия не может быть длиннее 128 символов",
        )
        await Customization.Menu.set()
        await answer_message(message, text=texts.Customize.ROOT, reply_markup=ikb_customize(user_config))
        return
        
    if value.strip() == "-":
        value = texts.GREETING_DEFAULT
    elif is_valid_russian_text(value):
        value = [dict(text=value)]
    else:
        await answer_message(
            message,
            text="Сообщение приветствия должно быть на русском языке",
        )
        await Customization.Menu.set()
        await answer_message(message, text=texts.Customize.ROOT, reply_markup=ikb_customize(user_config))
        return

    async with db.DatabaseApi().session():
        user: db.User = await get_user(message)
        await common.update_user_config(
            user, dict(
                VOX_GREETING=value,
            )
            )

    # To let the previous change be commited first
    async with db.DatabaseApi().session():
        user_config = await common.get_user_config(user)

    await answer_message(message, text="Сообщение приветствия успешно обновлено")
    await Customization.Menu.set()
    await answer_message(message, text=texts.Customize.ROOT, reply_markup=ikb_customize(user_config))


@postponed(Dispatcher.message_handler, state=Customization.Greeting, content_types=types.ContentTypes.VOICE)
async def customize_greeting_handler(message: types.Message, state: FSMContext):
    reply_wait: types.Message = await answer_message(message, text="Сообщение приветствия обновляется...")

    cached_path: str = f"tg_file/{message.voice.file_id}.ogg"

    public_url: str = await CloudStorageAPI().secure_upload_publish(
        cached_path,
        url=await message.voice.get_url(),
    )

    async with db.DatabaseApi().session():
        user: db.User = await get_user(message)
        await common.update_user_config(
            user, dict(
                VOX_GREETING=[dict(
                    text="Голосовое приветствие",
                    url=public_url,
                )],
            )
            )

    # To let the previous change be commited first
    async with db.DatabaseApi().session():
        user_config = await common.get_user_config(user)
        kb = ikb_customize(user_config)

    await reply_wait.delete()
    await answer_message(message, text="Сообщение приветствия успешно обновлено")
    await Customization.Menu.set()
    await answer_message(message, text=texts.Customize.ROOT, reply_markup=kb)


@postponed(Dispatcher.callback_query_handler, state=Customization.Menu, text='Change voice')
async def change_voice_handler(callback: types.CallbackQuery):
    await answer_message(callback.message, text=texts.CHOOSE_VOICE, reply_markup=ikb_voices())

    await callback.message.delete()
    await callback.answer()


@postponed(
    Dispatcher.callback_query_handler, lambda callback: callback.data[:6] == "Voice "
                                                        and callback.data[6:] in ASSISTANT_VOICES.keys(),
    state=Customization.Menu
    )
async def chosen_voice_handler(callback: types.CallbackQuery):
    voice_id = callback.data[6:]

    async with db.DatabaseApi().session():
        user: db.User = await get_user(callback)
        await common.update_user_config(user, dict(VOX_VOICE=voice_id))

    # To let the previous change be commited first
    async with db.DatabaseApi().session():
        user_config = await common.get_user_config(user)

    await answer_message(
        callback.message,
        text=f"{texts.VOICE_CHOOSE_SUCCESS} {ASSISTANT_VOICES[voice_id].name}"
        )
    await callback.message.delete()
    await Customization.Menu.set()
    await answer_message(
        callback.message, text=texts.Customize.ROOT,
        reply_markup=ikb_customize(user_config)
        )


def contains_only_russian_letters(name):
    for char in name:
        if not ('а' <= char <= 'я' or 'А' <= char <= 'Я' or char in ['ё', 'Ё']):
            return False
    return True

@postponed(Dispatcher.callback_query_handler, state=Customization.Menu, text='Change name')
async def customize_name_handler(callback: types.CallbackQuery):
    await Customization.Name.set()
    kb = ikb_cancel_customization()
    await answer_message(callback.message, text=texts.Customize.NAME, reply_markup=kb)
    await callback.answer()


@postponed(Dispatcher.message_handler, state=Customization.Name, content_types=types.ContentTypes.TEXT)
async def customize_name_handler(message: types.Message, state: FSMContext):
    name: str = message.text

    async with db.DatabaseApi().session():
        user: db.User = await get_user(message)
        user_config = await common.get_user_config(user)

    if len(name) > 32:
        await answer_message(
            message,
            text="Обращение не может быть длиннее 32 символов",
        )

        await Customization.Menu.set()
        await answer_message(message, text=texts.Customize.ROOT, reply_markup=ikb_customize(user_config))
        return

    if len(name.split()) != 1:
        await answer_message(
            message,
            text="Обращение должно состоять из единственного слова",
        )
        await Customization.Menu.set()
        await answer_message(message, text=texts.Customize.ROOT, reply_markup=ikb_customize(user_config))
        return
    
    if not contains_only_russian_letters(name):
        await answer_message(
            message,
            text="Обращение должно состоять исключительно из букв русского алфавита, без использования цифр, специальных символов или латинских букв.",
        )
        await Customization.Menu.set()
        await answer_message(message, text=texts.Customize.ROOT, reply_markup=ikb_customize(user_config))
        return

    customization = common.assistant_config.generate_replicas_customization(name)

    async with db.DatabaseApi().session() as session:
        user: db.User = await get_user(message)
        await common.update_user_config(user, customization | dict(USER_DISPLAY_NAME=name))
        await session.flush()

    async with db.DatabaseApi().session():
        user: db.User = await get_user(message)
        user_config = await common.get_user_config(user)
    
    await answer_message(message, text="Обращение успешно обновлено")
    await Customization.Menu.set()
    await answer_message(message, text=texts.Customize.ROOT, reply_markup=ikb_customize(user_config))


@postponed(Dispatcher.callback_query_handler, state=Customization.Menu, text='Change ignorelist')
async def customize_ignorelist_handler(callback: types.CallbackQuery):
    await Customization.Ignorelist.set()
    async with db.DatabaseApi().session():
        await answer_message(
            callback.message,
            text=texts.Customize.IGNORELIST,
            reply_markup=ikb_ignorelist(await common.get_user_config(await get_user(callback)))
        )
    await callback.answer()


@postponed(Dispatcher.callback_query_handler, cbd_ignorelist.filter(action="delete"), state=Customization.Ignorelist)
async def customize_ignorelist_delete_handler(callback: types.CallbackQuery, callback_data: dict):
    async with db.DatabaseApi().session():
        user: db.User = await get_user(callback)

        await common.update_user_ignore_list(user, callback_data["number"], action="remove")

    await answer_message(callback.message, text=f"Номер {callback_data['number']} успешно удален из игнор-листа")
    await callback.answer()
    await callback.message.delete()
    async with db.DatabaseApi().session():
        user: db.User = await get_user(callback)
        await Customization.Ignorelist.set()
        await answer_message(
            callback.message,
            text=texts.Customize.IGNORELIST,
            reply_markup=ikb_ignorelist(await common.get_user_config(user))
            )


@postponed(Dispatcher.callback_query_handler, cbd_ignorelist.filter(action="add"), state=Customization.Ignorelist)
async def customize_ignorelist_add_handler(callback: types.CallbackQuery, callback_data: dict):
    await Customization.IgnorelistAdd.set()

    kb = ikb_cancel_customization()
    await answer_message(callback.message, text=texts.Customize.IGNORELIST_ADD, reply_markup=kb)
    await callback.answer()


def is_valid_phone_number(number) -> bool:
    if number.startswith('8') and len(number) == 11:
        number = "+7" + number[1::]
    try:
        parsed_number = phonenumbers.parse(number, None)
        return phonenumbers.is_valid_number(parsed_number)
    except phonenumbers.phonenumberutil.NumberParseException:
        return False
    

@postponed(
    Dispatcher.message_handler, state=Customization.IgnorelistAdd,
    content_types=types.ContentTypes.TEXT | types.ContentTypes.CONTACT
    )
async def customize_ignorelist_add_handler(message: types.Message, state: FSMContext):
    number: str

    if message.content_type == types.ContentType.CONTACT:
        number = message.contact.phone_number
    else:
        number = message.text

    if not is_valid_phone_number(number):
        await answer_message(message, text="Невалидный номер телефона")
        await Customization.Ignorelist.set()
        async with db.DatabaseApi().session():
            user: db.User = await get_user(message)
            await answer_message(
                message, text=texts.Customize.IGNORELIST,
                reply_markup=ikb_ignorelist(await common.get_user_config(await get_user(message)))
                )
        return

    async with db.DatabaseApi().session() as session:
        user: db.User = await get_user(message)
        await common.update_user_ignore_list(user, number, action="add")
        await session.flush()

        user = await get_user(message)
        await answer_message(message, text=f"Номер {number} добавлен в игнор-лист")

        await Customization.Ignorelist.set()
        await answer_message(
            message, text=texts.Customize.IGNORELIST,
            reply_markup=ikb_ignorelist(await common.get_user_config(await get_user(message)))
            )
        
@postponed(Dispatcher.message_handler, state=Customization.IgnorelistAdd)
async def customize_ignorelist_add_handler(message: types.Message, state: FSMContext):
    await answer_message(message, text="Невалидный формат номера телефона")
    await Customization.Ignorelist.set()
    async with db.DatabaseApi().session():
        user: db.User = await get_user(message)
        await answer_message(
            message, text=texts.Customize.IGNORELIST,
            reply_markup=ikb_ignorelist(await common.get_user_config(await get_user(message)))
            )
    return

@postponed(Dispatcher.callback_query_handler, cbd_ignorelist.filter(action="back"), state=Customization.Ignorelist)
async def customize_ignorelist_back_handler(callback: types.CallbackQuery, callback_data: dict):
    async with db.DatabaseApi().session():
        await answer_message(
            callback.message,
            text=texts.Customize.ROOT,
            reply_markup=ikb_customize(await common.get_user_config(await get_user(callback)))
        )
    await callback.message.delete()
    await Customization.Menu.set()
    await callback.answer()


@postponed(Dispatcher.callback_query_handler, state=Customization.Menu, text='Change chatgpt')
async def customize_chatgpt_handler(callback: types.CallbackQuery):
    async with db.DatabaseApi().session():
        user: db.User = await get_user(callback)
        user_config = await common.get_user_config(user)

        await common.update_user_config(user, dict(
            CHATGPT_ENABLED=not user_config["CHATGPT_ENABLED"],
        ))

    # To let the previous change be commited first
    async with db.DatabaseApi().session():
        user_config = await common.get_user_config(user)
        kb = ikb_customize(user_config)

    await callback.message.edit_reply_markup(reply_markup=kb)
    await callback.answer()


@postponed(Dispatcher.callback_query_handler, state=Customization.Menu, text="Change chatgpt instructions")
async def customize_chatgpt_instructions_handler(callback: types.CallbackQuery):
    async with db.DatabaseApi().session():
        user: db.User = await get_user(callback)
        user_config = await common.get_user_config(user)

    await Customization.ChatGPTInstructions.set()
    kb = ikb_cancel_customization()
    chatgpt_instructions = user_config["CHATGPT_INSTRUCTIONS"]
    text = f"Текущая инструкция для ChatGPT: \"{chatgpt_instructions}\"\n\n" + texts.Customize.CHATGPT_INSTRUCTIONS
    await answer_message(callback.message, text=text, reply_markup=kb)
    await callback.answer()


@postponed(Dispatcher.message_handler, state=Customization.ChatGPTInstructions, content_types=types.ContentTypes.TEXT)
async def customize_chatgpt_instructions_handler(message: types.Message, state: FSMContext):
    value: str = message.text
    
    if value.strip() == "-":
        value = None
    
    async with db.DatabaseApi().session():
        user: db.User = await get_user(message)

        await common.update_user_config(user, dict(
            CHATGPT_INSTRUCTIONS=value,
        ))

    await message.delete()
    await Customization.Menu.set()
    async with db.DatabaseApi().session():
        user: db.User = await get_user(message)
        user_config = await common.get_user_config(user)
    await answer_message(message, text=f"Инструкции для ChatGPT обновлены")
    await answer_message(message, text=texts.Customize.ROOT, reply_markup=ikb_customize(user_config))


@postponed(Dispatcher.callback_query_handler, text='Turn off Busy')
async def turn_off_busy_handler(callback: types.CallbackQuery):
    edited_msg = await callback.message.edit_text(text='Вы точно хотите от нас уйти?(', reply_markup=ikb_turn_off_busy())
    await record_message(edited_msg, callback.message)


@postponed(Dispatcher.callback_query_handler, text='Stop Busy true')
async def turn_off_busy_handler(callback: types.CallbackQuery):
    await callback.message.delete()

    async with db.DatabaseApi().session():
        await unsubscribe(callback, reclaim_number=True)

    await answer_message(callback.message, text=texts.LEAVE_MESSAGE, reply_markup=kb_welcome())
    await bot.unpin_all_chat_messages(chat_id=callback.from_user.id)
    await Registration.Onboarding.set()
    await callback.answer()


@postponed(Dispatcher.callback_query_handler, text='Stop Busy false')
async def turn_off_busy_handler(callback: types.CallbackQuery):
    await callback.message.delete()
    await callback.answer()


# region incoming call machinery
# region state struct
@dataclasses.dataclass
class OngoingDialogData:
    STORAGE_KEY: typing.ClassVar[str] = "ongoing_call_data"

    number: str  # stripped
    call_id: str
    # message_id: int
    message_ids: list[int] = dataclasses.field(default_factory=list)
    # message_text: str
    message_lines: list[str] = dataclasses.field(default_factory=list)
    call_status: str = "[звонок продолжается...]"
    is_answer: bool = False
    is_connect: bool = False
    is_finish: bool = False

    # region serialization
    @classmethod
    def from_json(cls, json: dict[str, typing.Any]) -> OngoingDialogData:
        return OngoingDialogData(**json)

    def to_json(self) -> dict[str, typing.Any]:
        return dataclasses.asdict(self)

    @classmethod
    def state_load(cls, data: typing.Mapping[str, typing.Any]) -> OngoingDialogData:
        if cls.STORAGE_KEY not in data:
            raise RuntimeError("OngoingDialogData not found in state!")

        return OngoingDialogData.from_json(data[cls.STORAGE_KEY])

    def state_store(self, data: typing.MutableMapping[str, typing.Any]) -> None:
        data[self.STORAGE_KEY] = self.to_json()

    @classmethod
    def state_del(cls, data: typing.MutableMapping[str, typing.Any]) -> None:
        data.pop(cls.STORAGE_KEY, None)

    @classmethod
    @contextlib.contextmanager
    def in_state(cls, data: typing.MutableMapping[str, typing.Any]) -> typing.Generator[OngoingDialogData, None, None]:
        obj: OngoingDialogData = cls.state_load(data)

        yield obj

        obj.state_store(data)

    # endregion serialization

    @property
    def pretty_number(self) -> str:
        return prettify_number(self.number)

    def build_text(self) -> str:
        return "\n".join(
            [
                f"Звонок от {self.pretty_number}:",
                *self.message_lines,
                *([] if self.is_finish else [texts.ANSWER_PEOPLE]),
                "",
                f"<em>{self.call_status}<em>",
            ]
        )

    @staticmethod
    def _split_text(text: str, limit: int) -> typing.Generator[str, None, None]:
        buf: io.StringIO = io.StringIO()
        line_sz: int = 0

        for ch in itertools.chain(text, [None]):
            if buf.tell() >= limit or ch is None:
                to_offload: int = buf.tell() - line_sz
                buf.seek(0)

                if to_offload <= 0:
                    to_offload = limit
                    line_sz -= to_offload

                yield buf.read(to_offload)

                remaining: str = buf.read()
                # Note: not initialized in constructor because otherwise `tell()` wouldn't work as intended
                buf = io.StringIO()
                buf.write(remaining)

            if ch is None:
                break

            if ch == '\n':
                line_sz = 0
            else:
                line_sz += 1

            buf.write(ch)

    async def output(
        self,
        telegram_id: str,
        reply_markup: ReplyKeyboardMarkup | None | type(Ellipsis) = ...,
    ) -> None:
        TG_MESSAGE_SIZE_LIMIT = 4096

        if reply_markup is ...:
            reply_markup = None

            if not self.is_connect:
                reply_markup = ikb_incoming_call(with_write_answer=not self.is_answer)

        if bot.get_current() is None:
            # I guess they might be unset for when we come here from a voximplant callback...?
            bot.set_current(bot)
            dp.set_current(dp)

        # Note: guaranteed to be initialized after loop, because _split_text always makes at least one chunk
        msg: types.Message

        for i, chunk in enumerate(self._split_text(self.build_text(), TG_MESSAGE_SIZE_LIMIT)):
            if i < len(self.message_ids):
                msg = await bot.edit_message_text(
                    text=chunk,
                    chat_id=telegram_id,
                    message_id=self.message_ids[i],
                    reply_markup=reply_markup,
                )
                await record_message(msg, msg)
                self.message_ids[i] = msg.message_id  # Because, apparently, it might change?
                continue

            msg = await send_message(
                chat_id=telegram_id,
                text=chunk,
            )
            self.message_ids.append(msg.message_id)

        if reply_markup is not None:
            msg: types.Message | bool = await msg.edit_reply_markup(reply_markup)
            assert isinstance(msg, types.Message), f"Unexpected type: {type(msg)}"
            self.message_ids[-1] = msg.message_id

    @classmethod
    async def create(cls, number: str, call_id: str, telegram_id: str) -> OngoingDialogData:
        number = strip_number(number)

        result = OngoingDialogData(
            number=number,
            call_id=call_id,
        )

        await result.output(telegram_id)

        return result

    async def update_from_commands_refresh(
        self,
        telegram_id: str,
        commands: list[dict[str, typing.Any]],
        call_completed: bool = False,
    ) -> None:
        self.message_lines.clear()
        self.call_status = "[звонок продолжается...]"

        is_post_finish: bool = False
        is_post_connect: bool = False
        for command in commands:
            cmd_name: str = command["command"]
            if cmd_name == "message":
                side_icon: str
                if command["side"] == "caller":
                    side_icon = "👨"
                elif command["side"] == "user" and is_post_connect and not is_post_finish:
                    side_icon = "😎"
                else:  # robot or user before connect / after finish
                    side_icon = "🤖"

                cmd_text: str = command["text"]
                if command["type"] == "part":
                    cmd_text += "..."

                if command["side"] == "user" and is_post_finish:
                    cmd_text = f"<s>{cmd_text}</s>"

                self.append_line(f"{side_icon}: {cmd_text}")
            elif cmd_name == "connect":
                self.is_connect = True
                is_post_connect = True
                self.append_line("========================")
                self.call_status = "[идет соединение...]"
            elif cmd_name == "finish":
                self.is_finish = True
                is_post_finish = True
                self.call_status = "[собеседник положил трубку]"
            elif cmd_name == "answer":
                pass
            elif cmd_name == "busy":
                pass
            elif cmd_name == "recall":
                self.call_status = "[звонок завершен]"
            else:
                logging.error(
                    "Bad call command! Ignoring", extra=dict(
                        command=command,
                        telegram_id=telegram_id,
                        call_id=self.call_id,
                    )
                    )

        # Ellipsis means default here
        reply_markup: InlineKeyboardMarkup | None | type(Ellipsis) = ...

        if call_completed:
            async with db.DatabaseApi().session():
                user: db.User | None = await db.DatabaseApi().find_user(telegram_id=telegram_id)

                if user is not None and user.given_phone != "":
                    reply_markup = ikb_call_back()

        try:
            await self.output(
                telegram_id=telegram_id,
                reply_markup=reply_markup,
            )
        except exceptions.MessageNotModified:
            pass

    def append_line(self, line: str) -> None:
        self.message_lines.append(line)

    def replace_last_line(self, line: str) -> None:
        if not self.message_lines:
            return self.append_line(line)

        self.message_lines[-1] = line

    async def quick_update_refresh(
        self,
        command: typing.Literal["busy", "recall", "answer", "connect"],
        *,
        callback: types.CallbackQuery | None = None,
        telegram_id: str | None = None,
        answer_text: str | None = None,
    ) -> None:
        if callback and not telegram_id:
            telegram_id = callback.message.chat.id

        assert telegram_id is not None, "missing telegram_id"

        reply_markup: InlineKeyboardMarkup | None | type(Ellipsis) = ...

        if command == "busy":
            self.append_line("🤖: Абонент сейчас занят. Позвоните позже.")
            reply_markup = callback.message.reply_markup if callback else None
        elif command == "recall":
            self.append_line("🤖: К сожалению, абонент сейчас занят. Я передам, что вы звонили, и он вам перезвонит.")
            self.call_status = "[звонок завершен]"
            reply_markup = None
        elif command == "answer":
            assert answer_text is not None, "answer command missing answer_text"
            self.append_line(f"🤖: {answer_text}")
            reply_markup = ikb_incoming_call(with_write_answer=not self.is_answer)
        elif command == "connect":
            self.append_line("========================")
            self.call_status = "[идет соединение...]"
            reply_markup = None
        else:
            assert False, f"bad quick command: {command}"

        try:
            await self.output(telegram_id, reply_markup)
        except exceptions.MessageNotModified:
            logging.warning(
                f"Speculative call transcript update failed",
                exc_info=True,
                extra=dict(
                    call_id=self.call_id,
                    chat_id=telegram_id,
                    message_lines=self.message_lines,
                ),
            )


# endregion state struct


# region handlers
@postponed(Dispatcher.callback_query_handler, text='I am busy', state=ProfileIncomingCall.LineIsBusy)
async def busy_handler(callback: types.CallbackQuery, state: FSMContext):
    async with state.proxy() as data:
        with OngoingDialogData.in_state(data) as dialog_data:
            call_id = uuid.UUID(dialog_data.call_id)
            command = json.dumps(
                {
                    'command': 'busy', 'id': str(uuid.uuid4()), 'side': 'user',
                    'timestamp': str(datetime.timestamp(datetime.now())),
                }
            )
            await dialog_data.quick_update_refresh("busy", callback=callback)
            await tg_call_commands_queues[call_id].put(command)
    await callback.answer()


@postponed(Dispatcher.callback_query_handler, text='I am call back', state=ProfileIncomingCall.LineIsBusy)
async def call_back_handler(callback: types.CallbackQuery, state: FSMContext):
    async with state.proxy() as data:
        with OngoingDialogData.in_state(data) as dialog_data:
            call_id = uuid.UUID(dialog_data.call_id)
            command = json.dumps(
                {
                    'command': 'recall', 'id': str(uuid.uuid4()), 'side': 'user',
                    'timestamp': str(datetime.timestamp(datetime.now())),
                }
            )
            await dialog_data.quick_update_refresh("recall", callback=callback)
            await tg_call_commands_queues[call_id].put(command)
    await callback.answer()


@postponed(Dispatcher.callback_query_handler, text='Write answer', state=ProfileIncomingCall.LineIsBusy)
async def answer_button_handler(callback: types.CallbackQuery, state: FSMContext):
    async with state.proxy() as data:
        with OngoingDialogData.in_state(data) as dialog_data:
            call_id = uuid.UUID(dialog_data.call_id)
            command = json.dumps(
                {
                    'command': 'answer', 'id': str(uuid.uuid4()),
                    'timestamp': str(datetime.timestamp(datetime.now())),
                }
            )
            await tg_call_commands_queues[call_id].put(command)
            dialog_data.is_answer = True

    # Note: no need to re-output via dialog_data, I guess...
    await callback.message.edit_reply_markup(ikb_incoming_call(with_write_answer=False))
    mes = await answer_message(callback.message, text="Напишите в чат, что хотите передать")
    await callback.answer()
    await asyncio.sleep(10)
    await mes.delete()


@postponed(
    Dispatcher.message_handler, state=ProfileIncomingCall.LineIsBusy,
    content_types=types.ContentTypes.TEXT | types.ContentTypes.VOICE
    )
async def answer_handler(message: types.Message, state: FSMContext):
    await message.delete()
    async with state.proxy() as data:
        with OngoingDialogData.in_state(data) as dialog_data:
            call_id = uuid.UUID(dialog_data.call_id)

            if message.content_type == types.ContentType.TEXT and message.text[0] == '/':
                mes = await answer_message(message, text='Вы не можете пользоваться командами во время звонка')
                await asyncio.sleep(5)
                await mes.delete()
                return

            # Actually, screw this - I don't know why I should prevent it anyway
            # if not dialog_data.is_connect:
            #     return  # Messages before connection aren't supported

            text: str
            command: str
            if message.content_type == types.ContentType.VOICE:
                reply_wait: types.Message = await answer_message(message, text="Сообщение обрабатывается...")

                # TODO: Clean up later...?
                cached_path: str = f"tg_file/tmp_{message.voice.file_id}.ogg"

                audio_public_url: str = await CloudStorageAPI().secure_upload_publish(
                    cached_path,
                    url=await message.voice.get_url(),
                )

                await reply_wait.delete()

                # TODO: Extract the text for the readout
                text = "<i>Голосовое сообщение</i>"
                command = json.dumps(
                    {
                        'command': 'message',
                        'id': str(uuid.uuid4()),
                        'side': 'user',
                        'text': text,
                        'timestamp': str(datetime.timestamp(datetime.now())),
                        'type': 'whole',
                        'url': audio_public_url,
                    }
                )
            else:
                text = message.text
                command = json.dumps(
                    {
                        'command': 'message',
                        'id': str(uuid.uuid4()),
                        'side': 'user',
                        'text': text,
                        'timestamp': str(datetime.timestamp(datetime.now())),
                        'type': 'whole',
                    }
                )

            # Note: This code is shared between text and voice messages
            await dialog_data.quick_update_refresh("answer", answer_text=text, telegram_id=message.chat.id)
            await tg_call_commands_queues[call_id].put(command)


@postponed(Dispatcher.callback_query_handler, text='Connect', state=ProfileIncomingCall.LineIsBusy)
async def connect_handler(callback: types.CallbackQuery, state: FSMContext):
    async with state.proxy() as data:
        with OngoingDialogData.in_state(data) as dialog_data:
            call_id = uuid.UUID(dialog_data.call_id)
            command = json.dumps(
                {
                    'command': 'connect', 'destinationNumber': dialog_data.number, 'id': str(uuid.uuid4()),
                    'side': 'user', 'timestamp': str(datetime.timestamp(datetime.now())),
                }
            )
            await dialog_data.quick_update_refresh("connect", callback=callback)
            await tg_call_commands_queues[call_id].put(command)
    await callback.answer()


@postponed(Dispatcher.callback_query_handler, text='Callback after the end of call')
async def recall_handler(callback: types.CallbackQuery, state: FSMContext):
    async with db.DatabaseApi().session():
        has_virtual_number, has_service = await check_outgoing(callback, need_calls=True)

    if not has_virtual_number:
        await answer_message(callback.message, text=texts.CallChangeTariff.CALL)
    elif not has_service:
        await answer_message(callback.message, text=texts.NO_AVAILABLE_CALLS)
    else:
        number = callback.message.text.split('\n', 1)[0].removeprefix("Звонок от ").removesuffix(":")
        number = strip_number(number)
        async with state.proxy() as data:
            # Note: not dialog_data.number! It's passed to a completely different handler
            data['number'] = number
            await start_outbound_call(callback=callback, destination=number)
            await state.finish()

    await callback.answer()


@postponed(Dispatcher.callback_query_handler, text='Send message after the end of call')
async def resms_handler(callback: types.CallbackQuery, state: FSMContext):
    async with db.DatabaseApi().session():
        has_virtual_number, has_service = await check_outgoing(callback, need_messages=True)

    if not has_virtual_number:
        await answer_message(callback.message, text=texts.CallChangeTariff.SMS)
    elif not has_service:
        await answer_message(callback.message, text=texts.NO_AVAILABLE_SMS)
    else:
        number = callback.message.text.split('\n', 1)[0].removeprefix("Звонок от ").removesuffix(":")
        number = strip_number(number)

        async with state.proxy() as data:
            logging.info(f"message text to {number}")
            # Note: not dialog_data.number! It's passed to a completely different handler
            data['number'] = number
            await answer_message(
                callback.message, text=f"Сообщение будет отправлено на номер: {prettify_number(data['number'])}. "
                                       f"Введите текст сообщения",
                reply_markup=kb_cancel()
                )
            await ProfileSendMessage.GetMessage.set()
    await callback.answer()


# endregion handlers


# region callbacks
async def start_dialog(telegram_id: str, number: str, call_id: str):
    state = FSMContext(storage=dp.storage, chat=telegram_id, user=telegram_id)
    prev_state: str | None = await state.get_state()

    if prev_state == ProfileIncomingCall.LineIsBusy.state:
        # Note: also triggered by repeated requests from voximplant, so I have no choice but to ignore
        logging.warning(
            "Nested incoming call detected!",
            extra=dict(
                telegram_id=telegram_id,
                number=number,
                call_id=call_id,
            ),
        )
        async with state.proxy() as data:
            with OngoingDialogData.in_state(data) as dialog_data:
                if dialog_data.number != strip_number(number):
                    logging.error(
                        f"Concurrent call, support not yet implemented. Ignoring completely.",
                        extra=dict(
                            telegram_id=telegram_id,
                            number=number,
                            old_number=dialog_data.number,
                            call_id=call_id,
                            old_call_id=dialog_data.call_id,
                        ),
                    )
                    return

                # Note: shouldn't actually happen, but appears to.
                logging.warning(
                    "Duplicate reporting of identical call, updating state",
                    extra=dict(
                        telegram_id=telegram_id,
                        number=number,
                        call_id=call_id,
                        old_call_id=dialog_data.call_id,
                    ),
                )

    if prev_state is not None:
        async with db.DatabaseApi().session():
            user: db.User | None = await db.DatabaseApi().find_user(telegram_id=telegram_id)
            if user is not None and len(user.active_plans) > 0:
                reply_markup: ReplyKeyboardMarkup
                if user.given_phone == "":
                    reply_markup = kb_main_without_number()
                else:
                    reply_markup = kb_main_with_number()
                await send_message(chat_id=telegram_id, text="Поступил новый звонок!", reply_markup=reply_markup)
            del user

    async with state.proxy() as data:
        (await OngoingDialogData.create(number, call_id, telegram_id)).state_store(data)
        async with MultipleCallSynchronizationData.in_state(data, telegram_id, state) as call_sync:
            call_sync.calls_count += 1
            await state.set_state(ProfileIncomingCall.LineIsBusy.state)
            logging.info(f"start_dialog: {number=}, get_state()={await state.get_state()}, prev_state={prev_state}")


async def finish(telegram_id: str, commands: list, record: str):
    state = FSMContext(storage=dp.storage, chat=telegram_id, user=telegram_id)
    await process_command(telegram_id=telegram_id, request=commands, call_completed=True)

    if await state.get_state() != ProfileIncomingCall.LineIsBusy.state:
        logging.error(
            "finish() called outside of a call. Ignoring",
            stack_info=True,
            extra=dict(
                state=await state.get_state(),
            ),
        )
        return

    async with state.proxy() as data:
        with OngoingDialogData.in_state(data) as dialog_data:
            call_id = dialog_data.call_id
            await send_audio_dialog(telegram_id, record, dialog_data.number)

        # TODO: better ensure this doesn't cause any race conditions...
        await asyncio.sleep(3.)
        if call_id == OngoingDialogData.state_load(data).call_id:
            OngoingDialogData.state_del(data)

        async with MultipleCallSynchronizationData.in_state(data, telegram_id, state) as call_sync:
            call_sync.calls_count -= 1
            if call_sync.calls_count < 0:
                logging.error(
                    "Ongoing calls count below zero",
                    # probably multiple_calls_synchronization data was not found in state 
                    stack_info=True,
                    extra=dict(
                        state=await state.get_state(),
                        prev_telegram_id=call_sync.lock_id,
                        curr_telegram_id=telegram_id,
                        calls_count=call_sync.calls_count,
                    ),
                )
            call_sync.is_all_calls_finished = (call_sync.calls_count == 0)
                


async def process_command(telegram_id: str, request: list, call_completed=False):
    state = FSMContext(storage=dp.storage, chat=telegram_id, user=telegram_id)

    if await state.get_state() != ProfileIncomingCall.LineIsBusy.state:
        logging.error(
            "process_command() called outside of a call. Ignoring",
            stack_info=True,
            extra=dict(
                state=await state.get_state(),
            ),
        )
        return

    async with state.proxy() as data:
        with OngoingDialogData.in_state(data) as dialog_data:
            await dialog_data.update_from_commands_refresh(telegram_id, request, call_completed)


# endregion callbacks
# endregion incoming call machinery


# Note: this handler should be the last one
@postponed(Dispatcher.message_handler)
async def delete_command(message: types.Message):
    await message.delete()


# region uncategorized callbacks
async def show_incoming_message_to_user(telegram_id: str, number: str, text_message: str):
    correct_number = number.removeprefix('+')
    text = f'Сообщение от {prettify_number(correct_number)}\n'
    await send_message(chat_id=telegram_id, text=text + text_message)


async def unpaid_incoming_call_notification(telegram_id: str, number: str):
    kb, reason_text = await unpaid_notification_common(telegram_id)
    if kb is None:
        return

    number = strip_number(number)

    text = f'Вам звонили с номера {prettify_number(number)}, ' \
           f'{reason_text}'

    await send_message(chat_id=telegram_id, text=text, reply_markup=kb)


async def unpaid_incoming_sms_notification(telegram_id: str, number: str):
    kb, reason_text = await unpaid_notification_common(telegram_id)
    if kb is None:
        return

    number = strip_number(number)

    text = f'Вам пришло сообщение от {prettify_number(number)}, ' \
           f'{reason_text}'

    await send_message(chat_id=telegram_id, text=text, reply_markup=kb)


async def unpaid_notification_common(telegram_id: str):
    async with db.DatabaseApi().session():
        user: db.User = await db.DatabaseApi().find_user(telegram_id=telegram_id)
        assert user is not None

        unpaid_status = await common.get_unpaid_status(user)
        if unpaid_status == common.UnpaidStatus.NO_SUBSCRIPTION:
            # Ignore
            return None, None
        elif unpaid_status == common.UnpaidStatus.SUBSCRIPTION_UNPAID:
            kb = ikb_recurrent_fail()
            reason_text = 'но мы не можем предоставить вам услугу ввиду неуплаты по тарифу.\n' \
                          'Для возобновления работы оплатите сервис'
        elif unpaid_status == common.UnpaidStatus.OUT_OF_PLAN_EXTRA_UNPAID:
            kb = ikb_extra_fail()
            reason_text = 'но мы не можем предоставить вам услугу, поскольку у вас закончились лимит по тарифу.\n' \
                          'Для возобновления работы оплатите тариф Extra'
        elif unpaid_status == common.UnpaidStatus.OUT_OF_PLAN_NO_EXTRA_AUTOCHARGE:
            kb = None
            reason_text = 'но мы не можем предоставить вам услугу, поскольку у вас закончились лимит по тарифу.\n' \
                          'Для возобновления работы вы можете подключить автопродление в настройках'
        else:
            logging.warning("Unknown UnpaidStatus")
            return None, None

    return kb, reason_text


async def tell_mobile_auth_code(telegram_id: str, code: str):
    await send_message(
        chat_id=telegram_id,
        text=f"Ваш код для авторизации в мобильном приложении: `{code}`",
        parse_mode=ParseMode.MARKDOWN,
    )
# endregion uncategorized callbacks
