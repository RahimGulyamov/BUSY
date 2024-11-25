from __future__ import annotations

import logging
import typing
import asyncio

from aiocloudpayments import Router, Result
from aiocloudpayments.types import PayNotification, CancelNotification, \
    CheckNotification, RecurrentNotification, FailNotification
import aiogram.utils.exceptions as aiogram_exceptions

from ... import db
from .types import *
from ... import common

from ...telegram.main import \
    successful_subscription as tg_successful_subscription, \
    successful_payment_retry as tg_successful_payment_retry, \
    successful_payment as tg_successful_payment


cp_router: Router = Router()


@cp_router.check()
async def check_handler(notification: CheckNotification):
    logging.info(f"Got CP check notification: {notification}")
    async with db.DatabaseApi().session():
        user = await db.DatabaseApi().find_user(user_id=int(notification.account_id))
        if user is None:
            logging.warning(f"Tried to get payment from unknown user {notification.account_id}")
            return Result.WRONG_ACCOUNT_ID
    
    reason: int = notification.data["PaymentReason"]
    if reason not in set(PaymentReasons):
        logging.warning(f"Tried to get payment from user {notification.account_id} for no reason")
        return Result.WRONG_ORDER_NUMBER

    return Result.OK


@cp_router.pay()
async def pay_handler(notification: PayNotification):
    logging.info(f"Got CP pay notification: {notification}")

    reason: int = notification.data["PaymentReason"]
    plan_id: int = notification.data["Plan"]
    detached: bool = notification.data["Detached"]

    async with db.DatabaseApi().session():
        user = await db.DatabaseApi().find_user(user_id=int(notification.account_id))
        if user is None:
            logging.warning(f"Tried to get payment from unknown user {notification.account_id}")
            return Result.INTERNAL_ERROR

        if not detached:
            # Threat it just as notification
            return Result.OK

        plan: db.Plan = await db.DatabaseApi().get_plan(plan_id=plan_id)
        plan_price = plan.price

        user.payment_token = notification.token
        user.payment_method_string = f"{notification.card_type} " \
                                     f"{notification.card_first_six}****{notification.card_last_four}"
        payment_method_string = user.payment_method_string

        if reason == PaymentReasons.REGULAR_PLAN_SUBSCRIPTION:
            await common.change_subscription(user, plan, notification.transaction_id)
        elif reason == PaymentReasons.REGULAR_PLAN_MANUAL_RETRY:
            await common.renew_subscription(user, notification.transaction_id)
            user.extra_data = user.extra_data | {common.ExtraData.FAILED_RECURRENT_RECOVERED: True}
        elif reason == PaymentReasons.EXTRA_PLAN_MANUAL_RETRY or reason == PaymentReasons.EXTRA_PLAN:
            await common.activate_extra_plan(user, plan, notification.transaction_id)
            user.extra_data = user.extra_data | {common.ExtraData.FAILED_EXTRA_RECOVERED: True}
        elif reason == PaymentReasons.FREE_TRIAL_VERIFICATION_PAYMENT:
            from .methods import cp  # Quite dirty, but whatever
            refund_result = await cp.refund_payment(notification.transaction_id, notification.amount)
            logging.info("Refunded verification payment", extra=dict(
                user_id=user.id,
                user_name=user.get_pretty_name(),
                amount=notification.amount,
                original_transaction_id=notification.transaction_id,
                refund_transaction_id=refund_result.transaction_id
            ))
            await common.change_subscription(user, plan, notification.transaction_id, free_trial=True)
        else:
            logging.warning(f"Got payment from user {notification.account_id} for no reason")
            return Result.INTERNAL_ERROR

        user_tg_id = user.telegram_id
        user_given_phone = user.given_phone

    # Important to be outside DB session context
    if detached:
        # Trigger TG bot if the payment was performed through CP widget (not by payment token i.e. not synchronously)
        
        try:
            if reason == PaymentReasons.REGULAR_PLAN_SUBSCRIPTION or reason == PaymentReasons.FREE_TRIAL_VERIFICATION_PAYMENT:
                await tg_successful_subscription(user_tg_id, plan_id, user_given_phone)
            elif reason == PaymentReasons.REGULAR_PLAN_MANUAL_RETRY or reason == PaymentReasons.EXTRA_PLAN_MANUAL_RETRY:
                await tg_successful_payment_retry(user_tg_id, plan_id, plan_price, payment_method_string)
            elif reason == PaymentReasons.EXTRA_PLAN:
                await tg_successful_payment(user_tg_id, plan_id, plan_price)
        except aiogram_exceptions.BadRequest as e:
            logging.error("Failed to inform user of successful payment status", extra=dict(
                error=e,
                user_id=user.id,
                user_name=user.get_pretty_name(),
                plan_id=plan_id,
                payment_reason=reason,
            ))

    return Result.OK


__all__ = [
    "cp_router",
]
