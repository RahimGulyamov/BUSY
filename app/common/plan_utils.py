# Note: Not yet used. Intended as a refactoring of the existing model.

from __future__ import annotations
import typing
import logging
from dataclasses import dataclass, field
import datetime
import contextlib

import sqlalchemy
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.extra_data_utils import Json

from .. import db
from .. import common
from .extra_data_utils import *


class UserPlansUtil:
    """
    Manages the plans and subscriptions of a user.
    
    Must be used under a continuous db session
    """
    
    user: db.User
    session: AsyncSession
    
    def __init__(self, user: db.User):
        self.user = user
        self.session = db.DatabaseApi().cur_session
    
    def _verify(self) -> None:
        if self.session is not db.DatabaseApi().cur_session:
            raise RuntimeError("Must be used under a continuous db session")
    
    """
    Actions to support:
    
    [+] check for available call/sms
    [+] use up a call/sms
    [+] check availability of options (like dedicated number)
    [+] get remaining calls/sms
    [+] get plan
    [ ] subscribe to a plan (or change the subscription)
    [ ] bill the user (if necessary)
    """
    
    async def get_active_plans(self) -> list[db.ActivePlan]:
        """
        Returns the list of all active_plans relevant at this point in time.
        """
        
        self._verify()
        
        return await db.DatabaseApi().get_active_plans(user_id=self.user.id)
    
    async def get_main_active_plan(self) -> db.ActivePlan | None:
        """
        Gets the only active plan that isn't extra.
        
        If there's none, returns `None`.
        
        If there's more than one, logs a warning and returns one of them.
        """
        
        self._verify()
        
        results = [
            act_plan
            for act_plan in await self.get_active_plans()
            if not act_plan.plan.is_extra
        ]
        
        if len(results) == 0:
            return None
        
        if len(results) > 1:
            logging.warning("More than one main active plan at a time!", extra=dict(
                user_id=self.user.id,
                user_name=f"{self.user.first_name} {self.user.last_name}",
                main_plans=[(act_plan.plan_id, act_plan.plan.name) for act_plan in results],
            ))
        
        return results[0]
    
    async def get_subscription_plan(self) -> db.Plan | None:
        """
        Returns the plan currently designated as the user's subscription.
        
        Note that it may not be active -- for example, if it isn't paid for.
        
        Returns `None` if the user has no subscription.
        """
        
        self._verify()
        
        return self.user.subscription

    async def find_active_plan(
        self,
        *,
        with_calls: bool = False,
        with_messages: bool = False,
        # TODO: Support several options at once?
        with_option: db.Option | common.Options | None = None
    ) -> db.ActivePlan | None:
        """
        Finds any active plan that satisfies the requirements.
        """
        
        self._verify()
        
        if isinstance(with_option, common.Options):
            with_option = await self.session.get(db.Option, with_option)
        
        wanted_options: list[db.Option] = []
        if with_option is not None:
            wanted_options.append(with_option)
        
        for active_plan in await self.get_active_plans():
            if with_calls and active_plan.calls_left <= 0:
                continue
            
            if with_messages and active_plan.messages_left <= 0:
                continue
            
            if not active_plan.plan.options.issuperset(wanted_options):
                continue
            
            return active_plan

        return None
    
    async def get_virtual_number(self) -> str | None:
        """
        Get the assigned virtual number, if the user's plan supports it.
        
        Otherwise
        """
        
        self._verify()
        
        active_plan: db.ActivePlan | None = await self.find_active_plan(
            with_option=common.Options.VIRTUAL_NUMBER,
        )
        
        if active_plan is None:
            return None
        
        number: str = self.user.given_phone
        
        if not number:
            logging.warning("User has no virtual number when they should!", extra=dict(
                user_id=self.user.id,
                user_name=self.user.get_pretty_name(),
                active_plan_id=active_plan.id,
            ))
        
        return number

    async def bill_resource(
        self,
        *,
        charge_call: bool = False,
        charge_msg: bool = False
    ) -> bool:
        """
        Verifies that the user has sufficient resources left (calls or messages).
        
        If so, charges the user and returns `True`. Otherwise returns `False`.
        """
        
        self._verify()
        
        active_plan: db.ActivePlan | None = await self.find_active_plan(
            with_calls=charge_call,
            with_messages=charge_msg,
        )

        adv_state_util = AdvancedServiceStateUtil(self.user)       
        
        if (
            active_plan is None and
            self.user.extra_plan_autocharge and
            adv_state_util.try_use()
        ):
            # Provide one call in advance
            return True
        
        if active_plan is None:
            return False
        
        if charge_call:
            logging.info("-1 call")
            active_plan.calls_left -= 1
        
        if charge_msg:
            logging.info("-1 msg")
            active_plan.messages_left -= 1
        
        adv_state_util.reset()
        
        return True

    async def get_remaining_resources(self) -> RemainingResources:
        """
        Returns the remaining resources (calls and messages) for the user, across all active plans.
        """
        
        self._verify()
        
        active_plans: list[db.ActivePlan] = await self.get_active_plans()
        
        return RemainingResources(
            calls=sum(ap.calls_left for ap in active_plans),
            total_calls=sum(ap.plan.calls for ap in active_plans),
            messages=sum(ap.messages_left for ap in active_plans),
            total_messages=sum(ap.plan.messages for ap in active_plans),
        )

    async def charge_if_needed(self) -> bool:
        raise NotImplementedError()

    async def activate_plan(self) -> bool:
        raise NotImplementedError()


class AdvancedServiceStateUtil(BaseExtraDataUtil[db.User, common.AdvanceServiceState]):
    KEY: typing.ClassVar[str] = common.ExtraData.ADVANCED_SERVICE_STATE

    def initial_state(self) -> common.AdvanceServiceState:
        self._verify()
        
        return common.AdvanceServiceState.UNUSED
    
    def is_unused(self) -> bool:
        return self.get() == common.AdvanceServiceState.UNUSED
    
    def is_in_progress(self) -> bool:
        return self.get() == common.AdvanceServiceState.IN_PROGRESS
    
    def is_notified(self) -> bool:
        return self.get() == common.AdvanceServiceState.NOTIFIED
    
    def try_use(self) -> bool:
        if self.is_unused():
            return False
        
        self.set(common.AdvanceServiceState.IN_PROGRESS)
        
        return True

    def reset(self) -> None:
        self.set(common.AdvanceServiceState.UNUSED)


@dataclass
class RemainingResources:
    calls: int
    total_calls: int
    messages: int
    total_messages: int
    
    def __str__(self) -> str:
        return f"{self.calls}/{self.total_calls} calls, {self.messages}/{self.total_messages} messages"


__all__ = [
    "UserPlansUtil",
    "AdvancedServiceStateUtil",
    "RemainingResources",
]

