from __future__ import annotations
import typing

import logging
import asyncio
import dataclasses
import datetime
import abc

import sqlalchemy
from sqlalchemy.ext.asyncio import AsyncSession
from app import db


handlers: dict[str, typing.Callable] = {}
scheduled_tasks: dict[int, asyncio.Task] = {}


async def run() -> None:
    async with db.DatabaseApi().session():
        actions = await _get_scheduled_actions()
        for action in actions:
            task = asyncio.create_task(_scheduled_action_task(action.id, action.type, action.time, **action.args))
            scheduled_tasks[action.id] = task

    logging.info("Action scheduler initialized")

    try:
        # Interrupted by an exception, leading to our cleanup code
        while True:
            await asyncio.sleep(60)
    finally:
        for task in scheduled_tasks.values():
            task.cancel()


async def raw_schedule_action(action_type: str, time: datetime.datetime, **kwargs) -> int:
    async with db.DatabaseApi().session(allow_reuse=True) as session:
        action: db.ScheduledAction = db.ScheduledAction(
            time=time,
            user_id=kwargs['user_id'] if "user_id" in kwargs else None,
            type=action_type,
            done=False,
            args=kwargs,
        )
        session.add(action)

        # To retrieve action.id
        await session.flush()
        action_id = action.id

        task = asyncio.create_task(_scheduled_action_task(action_id, action_type, time, **kwargs))
        scheduled_tasks[action_id] = task

    return action_id


async def raw_cancel_action(action_id: int):
    async with db.DatabaseApi().session(allow_reuse=True):
        action: db.ScheduledAction | None = await db.DatabaseApi().get_scheduled_action(action_id=action_id)
        if action is None:
            logging.warning(f"Tried to cancel action with id {action_id} which doesn't exist")
            return

        if action.done:
            logging.warning(f"Tried to cancel action with id {action_id} which is already done")
            return
        else:
            action.done = True

        if action_id not in scheduled_tasks:
            logging.error(f"Action with id {action_id} is not planned by the scheduler somehow..")
            return

        scheduled_tasks[action_id].cancel()
        del scheduled_tasks[action_id]


def raw_action_handler(action_type: str):
    """
    Decorator for registering scheduled action handlers
    """
    
    def decorator(func: typing.Callable):
        handlers[action_type] = func
        return func

    return decorator


async def wait_until(dt):
    # sleep until the specified datetime
    now = datetime.datetime.now()
    await asyncio.sleep((dt - now).total_seconds())


async def _scheduled_action_task(action_id: int, action_type: str, dt: datetime.datetime, **kwargs):
    await wait_until(dt)

    try:
        await _perform_action(action_id, action_type, dt, **kwargs)
    except asyncio.CancelledError:
        pass
    except:
        logging.exception(f"Exception occurred during execution of scheduled task")


async def _perform_action(action_id: int, action_type: str, dt: datetime.datetime, **kwargs):
    if action_type not in handlers:
        logging.error(f"Handler is not set for scheduled actions of type {action_type}")
        return

    handler = handlers[action_type]
    await handler(dt, **kwargs)
    
    # TODO: Move this to a finally block? Or perhaps somehow mark the action as failed at least?
    async with db.DatabaseApi().session():
        action: db.ScheduledAction | None = await db.DatabaseApi().get_scheduled_action(action_id=action_id)
        if action is not None:
            action.done = True


async def _get_scheduled_actions() -> typing.List[db.ScheduledAction]:
    session: AsyncSession = db.DatabaseApi().cur_session
    query: sqlalchemy.Select = sqlalchemy.select(db.ScheduledAction).where(sqlalchemy.not_(db.ScheduledAction.done))
    return (await session.scalars(query)).all()


_T = typing.TypeVar("_T", bound="Action")


@dataclasses.dataclass
class Action(abc.ABC):
    _action_name_: typing.ClassVar[str]
    
    
    def __init_subclass__(cls, *, action_name: str, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        
        # Note: modifies cls, rather than creating anything new;
        #       also doesn't mind redundancy, as far as I can tell
        dataclasses.dataclass(cls)
        
        if action_name in handlers:
            raise ValueError(f"Action {action_name!r} already exists")
        
        cls._action_name_ = action_name
        
        def do_handle(dt: datetime.datetime, **kwargs) -> None:
            return cls.deserialize(kwargs).run(dt)
        
        handlers[action_name] = do_handle
    
    @abc.abstractmethod
    async def run(self, dt: datetime.datetime) -> None:
        """
        The actual logic for handling the action should be placed here.
        """
        
        ...
    
    async def schedule(self, time: datetime.datetime) -> ActionHandle:
        """
        A helper method that schedules the action to be run at the specified time.
        
        :return: The action id
        """
        
        return ActionHandle(await raw_schedule_action(
            self.get_name(),
            time,
            **self.serialize(),
        ))
    
    def serialize(self) -> dict[str, typing.Any]:
        """
        Serialize the action to json.
        """
        
        return dataclasses.asdict(self)
    
    @classmethod
    def deserialize(cls: typing.Type[_T], data: dict[str, typing.Any]) -> _T:
        """
        Deserialize the action from json.
        
        Note: the type is assumed to be known from another source.
        """
        
        fields: set[str] = {
            field.name
            for field in dataclasses.fields(cls)
        }
        
        for field_name in data:
            assert field_name in fields, f"Unknown field {field_name!r} for action {cls.__qualname__}"
        
        return cls(**data)
    
    @classmethod
    def get_name(cls) -> str:
        """
        Returns the action's name, used for storing it in the database.
        """
        
        return cls._action_name_


@dataclasses.dataclass
class ActionHandle:
    action_id: int
    
    async def cancel(self) -> None:
        await raw_cancel_action(self.action_id)
    
    async def get_action(self) -> db.ScheduledAction:
        action: db.ScheduledAction | None = await db.DatabaseApi().get_scheduled_action(action_id=self.action_id)
        
        if action is None:
            raise ValueError(f"Handle points to missing action: {self.action_id}")
        
        return action
    
    async def is_done(self) -> bool:
        return (await self.get_action()).done


__all__ = [
    "run",
    "Action",
    "ActionHandle",
    "raw_schedule_action",
    "raw_cancel_action",
    "raw_action_handler",
]


"""
Usage example:

Old style:

@scheduler.raw_action_handler("kek")
async def kek_handler(*, arg1, arg2, arg3 = None) -> None:
    print(arg1, arg2, arg3)

await scheduler.raw_schedule_action(
    "kek",
    datetime.datetime.now() + datetime.timedelta(seconds=30),
    arg1=12,
    arg2=13,
)

New style:

# Optionally, @dataclass here. Will be applied regardless
class KekAction(scheduler.Action, action_name="kek"):
    arg1: int
    arg2: int
    arg3: int | None = None
    
    async def run(self, dt: datetime.datetime) -> None:
        print(arg1, arg2, arg3)

await KekAction(12, 13).schedule(datetime.datetime.now() + datetime.timedelta(seconds=30))

"""