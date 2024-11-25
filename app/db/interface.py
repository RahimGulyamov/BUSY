from __future__ import annotations

import datetime
import typing
import uuid
from contextlib import asynccontextmanager
import logging
from contextvars import ContextVar, Token
import sqlalchemy
import sqlalchemy.orm
import sqlalchemy.engine
import sqlalchemy.ext.asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker, AsyncEngine
from sqlalchemy.orm import contains_eager

from . import model
from ..common.singleton import Singleton

from aiogram.dispatcher.storage import BaseStorage

# ContextVar's are recommended to be declared as module-level variables
_cur_session: ContextVar[AsyncSession] = ContextVar("_cur_session")


class DatabaseApi(Singleton):
    """
    A singleton wrapper around the app's database connection.
    Note that you may freely call `DatabaseApi()` to get the singleton instance.
    Alternatively, you may use `DatabaseApi._instance_` for that purpose.
    """

    engine: AsyncEngine
    _sessionmaker: async_sessionmaker
    
    def __init__(self) -> None:
        logging.info("Creating DB API instance")
        
        import config

        if None in [config.DB_USER, config.DB_PASS]:
            raise RuntimeError("No database credentials provided. Stopping.")

        connection_string: str = f"postgresql+asyncpg://{config.DB_USER}:{config.DB_PASS}@{config.DB_HOST}/{config.DB_NAME}"
        engine_args: dict[str, typing.Any] = getattr(config, "DATABASE_ENGINE_ARGS", {})

        self.engine = create_async_engine(connection_string, **engine_args)
        self._sessionmaker = async_sessionmaker(self.engine)

    @asynccontextmanager
    async def session(self,
                      autocommit: bool = True,
                      allow_reuse: bool = False,
                      ) -> typing.AsyncGenerator[AsyncSession]:
        """
        Usage: `async with db_api.session() as session: ...`
        
        Note that a nested call returns the same session as the outer call,
        and that it isn't closed automatically until the outermost call is exited.
        """

        # TODO: Reenable?        
        cur_session: AsyncSession | None = _cur_session.get(None)
        if allow_reuse and cur_session is not None:
            yield cur_session
            # await cur_session.commit()
            return
        del cur_session

        async with self._sessionmaker(expire_on_commit=False) as session:
            session: AsyncSession

            token = _cur_session.set(session)

            if autocommit:
                async with session.begin():
                    yield session
            else:
                yield session

            _cur_session.reset(token)

    @property
    def cur_session(self) -> AsyncSession:
        """
        Returns the session that's currently in use.
        This is only valid within the scope of a `db_api.session()` context.
        """

        return _cur_session.get()

    async def create_tables(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(model.Base.metadata.create_all)

            await conn.commit()

    async def dispose(self) -> None:
        # Intentionally not a `del` for a clearer diagnostic of use-after-dispose
        type(self)._instance_ = None
        await self.engine.dispose()

    async def put_user(self, user: model.User) -> None:
        session: AsyncSession = self.cur_session

        session.add(user)

    async def put_sms(self, sms: model.SMS) -> None:
        session: AsyncSession = self.cur_session

        session.add(sms)

    async def put_active_plan(self, active_plan: model.ActivePlan) -> None:
        session: AsyncSession = self.cur_session

        session.add(active_plan)

    async def put_command(self, command: model.Command) -> None:
        session: AsyncSession = self.cur_session

        session.add(command)

    async def find_user(self, *,
                        user_id: int | None = None,
                        own_phone: str | None = None,
                        given_phone: str | None = None,
                        telegram_id: int | str | None = None) -> model.User | None:
        session: AsyncSession = self.cur_session

        # TODO: Theoretically, given_phone might not be unique.
        #       If that's the case, this will currently return
        #       the user with the furthest subscription end date. 

        def enforce_single_key(**kwargs: typing.Any) -> None:
            assert sum(map(bool, kwargs.values())) == 1, f"Specify exactly one of {list(kwargs.keys())}"

        enforce_single_key(own_phone=own_phone, given_phone=given_phone, telegram_id=telegram_id, user_id=user_id)

        query: sqlalchemy.Select = sqlalchemy.select(model.User).limit(1)

        if own_phone is not None:
            query = query.where(model.User.own_phone == own_phone)
        elif user_id is not None:
            query = query.where(model.User.id == user_id)
        elif given_phone is not None:
            query = query.where(model.User.given_phone == given_phone)
        elif telegram_id is not None:
            if isinstance(telegram_id, int):
                telegram_id = str(telegram_id)
            query = query.where(model.User.telegram_id == telegram_id)

        return await session.scalar(query)

    async def get_plan(self, *, plan_id: int) -> model.Plan | None:
        session: AsyncSession = self.cur_session

        query: sqlalchemy.Select = sqlalchemy.select(model.Plan).where(model.Plan.id == plan_id)

        return await session.scalar(query)

    async def get_amo_contact(self, *, user_id: str) -> model.AmoContact | None:
        session: AsyncSession = self.cur_session

        query: sqlalchemy.Select = sqlalchemy.select(model.AmoContact).where(model.AmoContact.busy_user_id == user_id)

        return await session.scalar(query)

    async def get_amo_lead(self, *, lead_id: str = None, contact_id: str = None) -> model.AmoLead | None:
        session: AsyncSession = self.cur_session

        if lead_id is not None:
            query: sqlalchemy.Select = sqlalchemy.select(model.AmoLead).where(model.AmoLead.id == lead_id)
        else:
            query: sqlalchemy.Select = sqlalchemy.select(model.AmoLead).where(model.AmoLead.contact_id == contact_id)

        return await session.scalar(query)

    async def get_command(self, *, command_id: str) -> model.Plan | None:
        session: AsyncSession = self.cur_session

        query: sqlalchemy.Select = sqlalchemy.select(model.Command).where(model.Command.uid == command_id)

        return await session.scalar(query)

    async def get_option(self, *, option_id: int) -> model.Option | None:
        session: AsyncSession = self.cur_session
        query: sqlalchemy.Select = sqlalchemy.select(model.Option).where(model.Option.id == option_id)

        return await session.scalar(query)

    async def get_active_plan(self, *, user_id: int) -> model.ActivePlan | None:
        """
        Note: Do not use! (Outdated, also wrong)
        """
        
        session: AsyncSession = self.cur_session

        query: sqlalchemy.Select = sqlalchemy.select(model.ActivePlan).where(model.ActivePlan.user_id == user_id)

        return await session.scalar(query)

    async def get_active_plans(self, *, user_id: int) -> list[model.ActivePlan]:
        session: AsyncSession = self.cur_session
        
        now = datetime.datetime.now()
        query: sqlalchemy.Select = (
            sqlalchemy
            .select(model.ActivePlan)
            .where(
                model.ActivePlan.user_id == user_id,
                model.ActivePlan.start <= now,
                now < model.ActivePlan.end,
            )
        )

        return list((await session.scalars(query)).all())

    async def get_call_object(self, *, call_id: uuid.UUID | None = None,
                              session_id: str | None = None) -> model.Call | None:
        session: AsyncSession = self.cur_session

        if call_id is not None:
            query: sqlalchemy.Select = sqlalchemy.select(model.Call).where(model.Call.uid == call_id)
        elif session_id is not None:
            query: sqlalchemy.Select = sqlalchemy.select(model.Call).where(model.Call.session_id == session_id)
        else:
            return None

        return await session.scalar(query)

    async def get_devices(self, *, user_id: int) -> model.Device | None:
        session: AsyncSession = self.cur_session

        query: sqlalchemy.Select = sqlalchemy.select(model.Device).where(model.Device.id == user_id)

        return await session.scalar(query)

    async def get_device_info(self, *, device_uuid) -> model.Device | None:
        session: AsyncSession = self.cur_session
        return await session.scalar(
            sqlalchemy.select(model.Device)
            .where(model.Device.device_uuid == device_uuid)
        )
    
    async def change_device_registration_status(self, *, device_uuid, status=None) -> None:
        session: AsyncSession = self.cur_session
        #TODO: remove select request, just update dict in db
        device: model.Device | None = await self.get_device_info(device_uuid=device_uuid)

        if status is None:
            device.extra_data["registered"] = not device.extra_data.setdefault("registered", False)
        else:
            device.extra_data["registered"] = status

        await session.execute(
            sqlalchemy.update(model.Device)
            .where(model.Device.device_uuid == device_uuid)
            .values(extra_data=device.extra_data)
        )

    async def get_scheduled_action(self, *, action_id: int = None) -> model.ScheduledAction | None:
        session: AsyncSession = self.cur_session

        query: sqlalchemy.Select = sqlalchemy.select(model.ScheduledAction).where(model.ScheduledAction.id == action_id)

        return await session.scalar(query)

    async def find_scheduled_actions(self, *,
                                     user_id: int | None = None,
                                     done: bool | None = None,
                                     action_types: typing.List[str] | None = None
                                     ) -> typing.List[model.ScheduledAction]:
        session: AsyncSession = self.cur_session

        query: sqlalchemy.Select = sqlalchemy.select(model.ScheduledAction)

        if user_id is not None:
            query = query.where(model.ScheduledAction.user_id == user_id)
        if action_types is not None:
            query = query.where(model.ScheduledAction.type.in_(action_types))
        if done is not None:
            query = query.where(model.ScheduledAction.done == (sqlalchemy.true() if done else sqlalchemy.false()))

        return (await session.scalars(query)).all()

    async def has_current_call(self, *, user_id: int, time_period: datetime.datetime.minute = 5) -> bool:
        session: AsyncSession = self.cur_session
        current_time = datetime.datetime.now()
        search_time = current_time - datetime.timedelta(minutes=time_period)
        query: sqlalchemy.Select = sqlalchemy.select(model.Call)\
            .where(model.Call.user_id == user_id,
                   model.Call.timestamp > search_time,
                   model.Call.session_id == None,
                   sqlalchemy.not_(model.Call.finished))
        result = await session.scalars(query)
        logging.info(f'Result of has_current_call: {result}')

        return result is not None

    async def update_amo_tokens(
        self,
        access_token: str,
        refresh_token: str,
    ) -> None:
        tokens = await self.cur_session.scalar(select(model.AmoTokens))
        tokens.access_token, tokens.refresh_token = access_token, refresh_token

    async def get_amo_tokens(self) -> typing.Tuple[str, str]:
        tokens = await self.cur_session.scalar(select(model.AmoTokens))
        return tokens.access_token, tokens.refresh_token
    
    async def get_state(self, chat_id: str) -> model.StateRecord | None:
        return await self.cur_session.scalar(
             sqlalchemy.select(model.StateRecord)
            .where(
                model.StateRecord.chat_id == chat_id
            )
        )
    
    async def put_state(self, state: model.StateRecord) -> None:
        session: AsyncSession = self.cur_session
        session.add(state)

    async def update_state(self, 
                           chat_id: str, 
                           state: str | None = None, 
                           data: typing.Dict | None = None,
                           ) -> None:
        """
        Updates state with new state, if state is not None,
        and with new data, if data is not None. 
        You should check value existence before call.
        """

        if state is None and data is None:
            logging.warning(
                "Update request without state and data",
                stack_info=True,
                extra=dict(
                    chat_id=chat_id,
                ),
            )
            return
        updates = dict()

        if state is not None:
            updates['state'] = state
        
        if data is not None:
            updates['data'] = data
        
        session: AsyncSession = self.cur_session
        await session.execute(
            sqlalchemy.update(model.StateRecord)
            .where(model.StateRecord.chat_id==chat_id)
            .values(**updates)
        )


class DatabaseStorage(BaseStorage):
    """
    Database storage for FSM. Works with DatabaseApi.
    It stores current user's state and data for all steps 
    in states-storage.
    """

    async def close(self):
        """
        ???
        """
        pass

    async def wait_closed(self):
        """
        ???
        """
        pass


    async def get_state(self, *,
                        chat: typing.Union[str, int, None] = None,
                        user: typing.Union[str, int, None] = None,
                        default: str | None = None) -> str | None:
        """
        Get current state of user in chat. Return `default` if no record is found.
        """
        chat_id, _ = map(str, self.check_address(chat=chat, user=user))
        async with DatabaseApi().session():
            state_obj = await DatabaseApi().get_state(chat_id=chat_id)
            if state_obj is None:
                return self.handle_empty(self.resolve_state(default))
            state = self.handle_empty(self.resolve_state(state_obj.state))
            return state or self.handle_empty(self.resolve_state(default))

    async def get_data(self, *,
                       chat: typing.Union[str, int, None] = None,
                       user: typing.Union[str, int, None] = None,
                       default: typing.Dict | None = None) -> typing.Dict:
        """
        Get state-data for user in chat. Return `default` if no data is provided in storage.
        """
        chat_id, _ = map(str, self.check_address(chat=chat, user=user))
        async with DatabaseApi().session():
            state_obj = await DatabaseApi().get_state(chat_id=chat_id)
            if state_obj is None:
                return default or {}
            data = state_obj.data
            return data or default or {}
        
    async def put_if_not_exist(self, *,
                               chat_id: str,
                               state: str | None = None,
                               data: typing.Dict | None = None) -> bool:
        """
        Put new state with data into database if it is not exist. 
        Returns True, if added new state, and False otherwise.
        """
        async with DatabaseApi().session():
            state_obj = await DatabaseApi().get_state(chat_id=chat_id)
            if state_obj is None:
                user_id: model.User | None = (await DatabaseApi().find_user(telegram_id=chat_id)).id
                if user_id is None:
                    logging.error(
                        "User not found while putting new state with data",
                        stack_info=True,
                        extra=dict(
                            chat_id=chat_id,
                            state=self.resolve_state(state),
                            data=data or {}
                        ),
                    )
                    return
                
                await DatabaseApi().put_state(
                    model.StateRecord(
                        user_id=user_id,
                        chat_id=chat_id,
                        state=self.resolve_state(state),
                        data={}
                    )
                )
                return True
            else:
                return False

    async def set_state(self, *,
                        chat: typing.Union[str, int, None] = None,
                        user: typing.Union[str, int, None] = None,
                        state: str | None = None):
        """
        Set new state for user in chat.
        """
        chat_id, _ = map(str, self.check_address(chat=chat, user=user))
        if not await self.put_if_not_exist(chat_id=chat_id, state=state):
            async with DatabaseApi().session():
                await DatabaseApi().update_state(chat_id=chat_id, state=self.resolve_state(state), data=None)

    async def set_data(self, *,
                       chat: typing.Union[str, int, None] = None,
                       user: typing.Union[str, int, None] = None,
                       data: typing.Dict | None = None):
        """
        Set data for user in chat.
        """
        chat_id, _ = map(str, self.check_address(chat=chat, user=user))
        if not await self.put_if_not_exist(chat_id=chat_id, data=data):
            async with DatabaseApi().session():
                await DatabaseApi().update_state(chat_id=chat_id, state=None, data=data or {})

    @staticmethod
    def resolve_state(value) -> str:
        value = BaseStorage.resolve_state(value)

        if value is None:
            return ""
        else:
            return value

    @staticmethod
    def handle_empty(value: str) -> str | None:
        if value == "":
            return None
        else:
            return value



__all__ = [
    "DatabaseApi",
    "DatabaseStorage",
]
