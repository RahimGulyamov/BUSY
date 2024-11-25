from __future__ import annotations
import typing
import enum

from aiocloudpayments.exceptions import CpPaymentError


class PaymentReasons(enum.IntEnum):
    # First charge for new subscription
    REGULAR_PLAN_SUBSCRIPTION = 1
    # Recurrent charge for subscription (or auto retry after unsuccessful charge)
    REGULAR_PLAN_RECURRENT = 2
    # Manual retry after unsuccessful charge
    REGULAR_PLAN_MANUAL_RETRY = 3
    # Auto retry after unsuccessful charge
    REGULAR_PLAN_AUTO_RETRY = 4
    # Auto charge for extra plan
    EXTRA_PLAN = 5
    # Manual retry after unsuccessful charge
    EXTRA_PLAN_MANUAL_RETRY = 6
    # Auto retry after unsuccessful charge
    EXTRA_PLAN_AUTO_RETRY = 7
    # Verification payment; immediately refunded
    FREE_TRIAL_VERIFICATION_PAYMENT = 8
    
    def is_manual(self) -> bool:
        """
        Returns whether the payment is initiated directly by a user.
        
        If not, it is initiated by the system, either as a one-off or based on a schedule.
        """
        
        return self in (
            PaymentReasons.REGULAR_PLAN_SUBSCRIPTION,
            PaymentReasons.REGULAR_PLAN_MANUAL_RETRY,
            PaymentReasons.EXTRA_PLAN_MANUAL_RETRY,
            PaymentReasons.FREE_TRIAL_VERIFICATION_PAYMENT,
        )
    
    def is_scheduled(self) -> bool:
        """
        Returns whether the payment is initiated based on a schedule.
        
        Cannot be true for a manual payment.
        """
        
        return self in (
            PaymentReasons.REGULAR_PLAN_RECURRENT,
            PaymentReasons.REGULAR_PLAN_MANUAL_RETRY,
            PaymentReasons.REGULAR_PLAN_AUTO_RETRY,
        )


__all__ = [
    "PaymentReasons",
]
