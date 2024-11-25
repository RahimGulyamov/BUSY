from __future__ import annotations

from datetime import datetime, date, timedelta
import typing
import logging
import enum

from aiocloudpayments.types import Order, Subscription, Transaction
from aiocloudpayments import AioCpClient
from aiocloudpayments.exceptions import CpPaymentError, CpAPIError
from aiocloudpayments.endpoints.payments.tokens.charge import CpTokensChargeEndpoint

from ... import db
from ... import common
from .types import PaymentReasons


# TODO: Encapsulate these, instead of using bare globals?
cp: AioCpClient

# Workaround for IDE typechecker. Does nothing when the code is run
if typing.TYPE_CHECKING:
    cp = AioCpClient()


# NOTE: Must be called under existing db session!
async def create_order(user: db.User, plan: db.Plan, reason: int, price_override: int | None = None) -> Order:
    # Checks for the active session
    db.DatabaseApi().cur_session
    
    if price_override is None:
        price_override = plan.price
    
    order = await cp.create_order(
        amount=price_override,
        description=f"Подписка на сервис Busy, тариф {plan.name}",
        account_id=user.id,
        currency="RUB",
        culture_name="ru-RU",
        require_confirmation=False,
        json_data={
            "Plan": plan.id,
            "PaymentReason": reason,
            "Detached": True,
        },
        send_sms=False,
        send_email=False,
        send_viber=False,
        phone=user.own_phone,
    )

    await cancel_order(user)
    user.pending_payment_id = order.id
    return order


# NOTE: Must be called under existing db session!
async def cancel_order(user: db.User) -> None:
    # Checks for the active session
    db.DatabaseApi().cur_session
    
    if user.pending_payment_id is None:
        return

    try:
        await cp.cancel_order(user.pending_payment_id)
    except CpPaymentError:
        pass
    except CpAPIError:
        pass

    user.pending_payment_id = None


# NOTE: Must be called under existing db session!
async def charge(user: db.User, plan: db.Plan, reason: int) -> Transaction:
    # Checks for the active session
    db.DatabaseApi().cur_session
    
    assert user.payment_token is not None, \
        f"User {user.id} ({user.get_pretty_name()}) missing payment token"
    
    description = f"Подписка на сервис Busy, тариф: {plan.name}"
    
    if reason == PaymentReasons.FREE_TRIAL_VERIFICATION_PAYMENT:
        description += f"\n(Подтверждение платёжного метода)"
    
    # Mandated by new cloudpayments policies
    # TODO: Once aiocloudpayments is updated, remove the hacks below and use the proper API
    tr_initiator_code: TrInitiatorCode = (
        TrInitiatorCode.CLIENT_INITIATED
        if reason.is_manual() else
        TrInitiatorCode.SERVICE_INITIATED
    )
    payment_scheduled: PaymentScheduled = (
        PaymentScheduled.SCHEDULED
        if reason.is_scheduled() else
        PaymentScheduled.ONCE
    )
    
    # return await cp.charge_token(
    endpoint = CpTokensChargeUpdatedEndpoint(
        amount=plan.price,
        description=description,
        account_id=user.id,
        currency="RUB",
        json_data={
            "Plan": plan.id,
            "PaymentReason": reason,
            "Detached": False,
        },
        token=user.payment_token,
        tr_initiator_code=tr_initiator_code,
        payment_scheduled=payment_scheduled,
    )
    
    return await cp.request(endpoint)


# region temporary workaround
class TrInitiatorCode(enum.IntEnum):
    SERVICE_INITIATED = 0
    CLIENT_INITIATED = 1


class PaymentScheduled(enum.IntEnum):
    ONCE = 0
    SCHEDULED = 1


class CpTokensChargeUpdatedEndpoint(CpTokensChargeEndpoint):
    tr_initiator_code: int
    payment_scheduled: int = None  # Note: required if tr_initiator_code is SERVICE_INITIATED
# endregion