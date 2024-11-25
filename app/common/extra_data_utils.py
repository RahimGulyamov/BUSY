from __future__ import annotations
import typing
import logging
from dataclasses import dataclass, field
import abc

import sqlalchemy
from sqlalchemy.ext.asyncio import AsyncSession

from .. import db
from .. import common


# A convenience typehint
Json: typing.TypeAlias = int | str | bool | float | None | dict[str, "Json"] | list["Json"]


ModelT = typing.TypeVar("ModelT", bound=db.Base)
ValueT = typing.TypeVar("ValueT", bound=Json)


class BaseExtraDataUtil(abc.ABC, typing.Generic[ModelT, ValueT]):
    """
    A base class for utility objects that manage an entry in a database object's `extra_data`.
    
    `KEY` is the key used in the `extra_data` dict.
    """
    
    KEY: typing.ClassVar[str]
    
    obj: ModelT
    session: AsyncSession
    
    def __init__(self, obj: ModelT):
        self.obj = obj
        self.session = db.DatabaseApi().cur_session
    
    def _verify(self) -> None:
        if self.session is not db.DatabaseApi().cur_session:
            raise RuntimeError(f"{type(self).__qualname__} must be used under a continuous db session")
    
    @abc.abstractmethod
    def initial_state(self) -> ValueT:
        """
        Creates the initial data state if it isn't already present. Should be json-serializable.
        """
    
    def get(self) -> ValueT:
        """
        Returns the current data state. Should be json-serializable.
        """
        
        self._verify()
        
        extra_data: dict[str, Json] = self.obj.extra_data
        
        if self.KEY not in extra_data:
            extra_data[self.KEY] = self.initial_state()
        
        return extra_data[self.KEY]
    
    def set(self, new_state: ValueT) -> None:
        """
        Sets the new data state. Should be json-serializable.
        """
        
        self._verify()
        
        # To properly mark it as dirty
        self.obj.extra_data = self.obj.extra_data | {self.KEY: new_state}


class UserFreeTrialUtil(BaseExtraDataUtil[db.User, bool]):
    KEY = "used_free_trial"
    
    def initial_state(self) -> bool:
        return False
    
    def can_use(self) -> bool:
        return not self.get()
    
    def mark_used(self) -> None:
        if not self.can_use():
            logging.warning("Race condition or fraud: free trial used more than once!", extra=dict(
                user_id=self.obj.id,
                user_name=self.obj.get_pretty_name(),
            ))
        
        self.set(True)


__all__ = [
    "Json",  # TODO: Move to another module?
    "BaseExtraDataUtil",
    "UserFreeTrialUtil",
]

