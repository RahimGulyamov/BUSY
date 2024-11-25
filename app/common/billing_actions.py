from __future__ import annotations

import asyncio
import typing

import datetime
import json
import logging
import dateutil.parser
import functools
from dataclasses import dataclass

from ..scheduler import *
from .. import common
from .. import db

from ..api.cloudpayments import methods as cp_methods
from ..api.cloudpayments import types as cp_types
from aiocloudpayments.exceptions import CpPaymentError
import aiogram.utils.exceptions as aiogram_exceptions


class BillingActions:
    RecurrentPayment = "recurrent_payment"
    RecurrentPaymentRetry = "recurrent_payment_retry"
    KickInactive = "pishov_nahui"
    ExtraPlanPaymentRetry = "extra_plan_payment_retry"
    ExtraPlanReset = "extra_plan_reset"


@dataclass  # Note: redundant, but helps with type hints a bit
class RecurrentPaymentAction(Action, action_name=BillingActions.RecurrentPayment):
    user_id: int
    retries_left: int
    
    async def run(self, dt: datetime.datetime) -> None:
        from ..telegram.main import \
            successful_payment as tg_successful_payment, \
            unsuccessful_payment as tg_unsuccessful_payment

        async with db.DatabaseApi().session():
            user: db.User | None = await db.DatabaseApi().find_user(user_id=self.user_id)
            if user is None:
                logging.warning(f"User {self.user_id} not found")
                return
            telegram_id: str | None = user.telegram_id

            # await db.DatabaseApi().get_plan(plan_id=plan_id)
            plan: db.Plan | None = user.subscription
            
            if not plan:
                logging.warning(f"No plan (subscription) for user {self.user_id} ({user.get_pretty_name()}) -- cannot make recurrent payment")
                return
            
            plan_id: int = plan.id
            plan_price: int | None = plan.price
            assert plan_price, f"Price not set for plan {plan_id}"

            try:
                tx = await cp_methods.charge(user, plan, cp_types.PaymentReasons.REGULAR_PLAN_RECURRENT)
                next_active_plan_start = await common.activate_plan(user, plan, tx.transaction_id)
                
                self.retries_left = common.CHARGE_RETRIES_COUNT
                await self.schedule(next_active_plan_start)
                
                user.extra_data = user.extra_data | {common.ExtraData.FAILED_RECURRENT_RECOVERED: True}
                success = True
            except CpPaymentError:
                if self.retries_left > 0:
                    self.retries_left -= 1
                    await self.schedule(dt + common.CHARGE_RETRY_PERIOD)
                else:
                    await KickInactiveAction(self.user_id).schedule(dt + common.AUTO_KICK_PERIOD)

                user.extra_data = user.extra_data | {common.ExtraData.FAILED_RECURRENT_RECOVERED: False}
                success = False

        if telegram_id is not None:
            try:
                if success:
                    await tg_successful_payment(telegram_id, plan_id, plan_price)
                else:
                    await tg_unsuccessful_payment(telegram_id, plan_id, plan_price, is_extra=False)
            except aiogram_exceptions.BadRequest as e:
                logging.error("Failed to inform user of payment status", extra=dict(
                    error=e,
                    user_id=user.id,
                    user_action_name=user.get_pretty_name(),
                    plan_id=plan_id,
                    success=success,
                ))


@raw_action_handler(BillingActions.RecurrentPaymentRetry)
async def _deprecated_retry_rec_payment_handler(
    dt: datetime.datetime, *, user_id: int, retries_left: int
) -> None:
    logging.warning(
        f"Deprecated action {BillingActions.RecurrentPaymentRetry!r} used; "
        f"running as {RecurrentPaymentAction!r}"
    )
    await RecurrentPaymentAction(user_id=user_id, retries_left=retries_left).run(dt)


@dataclass  # Note: redundant, but helps with type hints a bit
class ExtraPlanPaymentRetryAction(Action, action_name=BillingActions.ExtraPlanPaymentRetry):
    user_id: int
    retries_left: int
    deadline: str
    
    async def run(self, dt: datetime.datetime) -> None:
        from ..telegram.main import \
            successful_payment as tg_successful_payment, \
            unsuccessful_payment as tg_unsuccessful_payment

        deadline: datetime.datetime = dateutil.parser.parse(self.deadline)

        # TODO: Why do we have both this check and a later one?
        if datetime.datetime.now() > deadline:
            # Extra plan is not actual anymore
            return

        async with db.DatabaseApi().session():
            user: db.User | None = await db.DatabaseApi().find_user(user_id=self.user_id)
            if user is None:
                logging.warning(f"User {self.user_id} not found")
                return
            
            telegram_id = user.telegram_id

            if datetime.datetime.now() > deadline:
                # Extra plan is not actual anymore
                logging.info(f"Extra plan payment expired for user {self.user_id}")
                user.extra_data = user.extra_data | {common.ExtraData.ADVANCED_SERVICE_STATE: common.AdvanceServiceState.UNUSED}
                return

            extra_plan: db.Plan = await db.DatabaseApi().get_plan(plan_id=common.Plans.EXTRA)
            plan_price = extra_plan.price
            plan_id = extra_plan.id

            try:
                tx = await cp_methods.charge(user, extra_plan, cp_types.PaymentReasons.EXTRA_PLAN_AUTO_RETRY)
                await common.activate_extra_plan(user, extra_plan, tx.transaction_id)
                success = True
            except CpPaymentError:
                if self.retries_left > 0:
                    self.retries_left -= 1
                    await self.schedule(dt + common.CHARGE_RETRY_PERIOD)
                success = False

        if telegram_id is not None:
            try:
                if success:
                    await tg_successful_payment(telegram_id, plan_id, plan_price)
                else:
                    await tg_unsuccessful_payment(telegram_id, plan_id, plan_price, is_extra=True)
            except aiogram_exceptions.BadRequest as e:
                logging.error("Failed to inform user of payment status", extra=dict(
                    error=e,
                    user_id=user.id,
                    user_action_name=user.get_pretty_name(),
                    plan_id=plan_id,
                    success=success,
                ))


@dataclass  # Note: redundant, but helps with type hints a bit
class ExtraPlanResetAction(Action, action_name=BillingActions.ExtraPlanReset):
    user_id: int
    
    async def run(self, dt: datetime.datetime) -> None:
        async with db.DatabaseApi().session():
            user: db.User | None = await db.DatabaseApi().find_user(user_id=self.user_id)
            if user is None:
                logging.warning(f"User {self.user_id} not found")
                return

            # Extra plan is not actual anymore
            logging.info(f"Extra plan payment expired for user {self.user_id}")
            user.extra_data = user.extra_data | {common.ExtraData.ADVANCED_SERVICE_STATE: common.AdvanceServiceState.UNUSED}


@dataclass  # Note: redundant, but helps with type hints a bit
class KickInactiveAction(Action, action_name=BillingActions.KickInactive):
    user_id: int
    
    async def run(self, dt: datetime.datetime) -> None:
        from ..telegram.main import user_kicked as tg_user_kicked

        async with db.DatabaseApi().session():
            user: db.User | None = await db.DatabaseApi().find_user(user_id=self.user_id)
            if user is None:
                logging.warning(f"User {self.user_id} not found")
                return
            
            has_number: bool = (user.given_phone != "")
            plan_id: int | None = user.subscription_id
            telegram_id: str | None = user.telegram_id
            
            if plan_id is None:
                logging.warning(f"No plan for user {self.user_id} ({user.get_pretty_name()}) -- cannot kick..?")
                return

            # In order to not cancel ourselves
            await common.unsubscribe(user, cancel_actions=False)

        if telegram_id is not None:
            await tg_user_kicked(telegram_id, plan_id, has_number)

        # Now can (relatively) safe cancel all billing actions
        async def cancel_billing_actions_delayed():
            # I hope that after this delay current action will be marked as finished
            await asyncio.sleep(10)
            await cancel_billing_actions(self.user_id)

        asyncio.create_task(cancel_billing_actions_delayed())


async def cancel_billing_punishment(user_id: int):
    async with db.DatabaseApi().session(allow_reuse=True):
        billing_punishments = await db.DatabaseApi().find_scheduled_actions(
            user_id=user_id,
            done=False,
            action_types=[
                BillingActions.KickInactive,
                BillingActions.RecurrentPaymentRetry,
                BillingActions.ExtraPlanPaymentRetry,
            ],
        )

        # logging.info(billing_punishments)

        for punishment in billing_punishments:
            await raw_cancel_action(punishment.id)


async def cancel_extra_punishments(user_id: int):
    async with db.DatabaseApi().session(allow_reuse=True):
        billing_actions = await db.DatabaseApi().find_scheduled_actions(
            user_id=user_id,
            done=False,
            action_types=[
                BillingActions.ExtraPlanPaymentRetry,
            ],
        )

        # logging.info(billing_actions)

        for billing_action in billing_actions:
            await raw_cancel_action(billing_action.id)


async def cancel_billing_actions(user_id: int):
    async with db.DatabaseApi().session(allow_reuse=True):
        billing_actions = await db.DatabaseApi().find_scheduled_actions(
            user_id=user_id,
            done=False,
            action_types=[
                BillingActions.KickInactive,
                BillingActions.RecurrentPayment,
                BillingActions.RecurrentPaymentRetry,
                BillingActions.ExtraPlanPaymentRetry,
            ],
        )

        # logging.info(billing_actions)

        for billing_action in billing_actions:
            await raw_cancel_action(billing_action.id)
