from __future__ import annotations
import typing
import datetime
from dataclasses import dataclass
import uuid
import sqlalchemy
import sqlalchemy.orm
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.ext.associationproxy import association_proxy

from aiogram.dispatcher.filters.state import State


class Base(sqlalchemy.orm.DeclarativeBase):
    # TODO: Auto-populate defaults, at least for the simple cases
    
    pass


class User(Base):
    __tablename__: typing.Final[str] = "users"
    
    # sqlalchemy.Column(sqlalchemy.Integer, primary_key=True, autoincrement=True, nullable=False)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    
    # The length of a phone number without spaces, parentheses and hyphens is up to 12.
    # The extra 8 bytes are reserved for, potentially, an extension code
    # Also, to clarify: own_phone is the phone used at registration; given_phone is the phone we provide, if any
    # sqlalchemy.Column(sqlalchemy.String(20), nullable=False, default="", index=True, unique=True)
    own_phone: Mapped[str] = mapped_column(sqlalchemy.String(20), default="", index=True, unique=True)
    # sqlalchemy.Column(sqlalchemy.String(20), nullable=False, default="", index=True)
    given_phone: Mapped[str] = mapped_column(sqlalchemy.String(20), default="", index=True)
    
    # Telegram ids currently can reach 5e10, which is more than 2^32,
    # so we use a 16-byte string, giving a 1e6 times larger range than needed
    # sqlalchemy.Column(sqlalchemy.String(16), nullable=True, default=None, index=True, unique=True)
    telegram_id: Mapped[str | None] = mapped_column(sqlalchemy.String(16), default=None, index=True, unique=True)
    
    # sqlalchemy.Column(sqlalchemy.String(48), nullable=True, default=None)
    first_name: Mapped[str | None] = mapped_column(sqlalchemy.String(48), default=None)
    # sqlalchemy.Column(sqlalchemy.String(48), nullable=True, default=None)
    last_name: Mapped[str | None] = mapped_column(sqlalchemy.String(48), default=None)

    gender: Mapped[str | None] = mapped_column(sqlalchemy.String(16), default=None)

    
    # sqlalchemy.Column(sqlalchemy.JSON, nullable=False, default={})
    extra_data: Mapped[dict] = mapped_column(sqlalchemy.JSON, default={})

    # Main plan for auto-renewal (e.g. Very/Super/Ultra Busy)
    # Null means no subscription
    # Note: NOT redundant: This represent what the user should be charged for regularly;
    #       Active plans represent what the user currently has (i.e. has paid for)
    subscription_id: Mapped[int | None] = mapped_column(sqlalchemy.ForeignKey("plans.id"))
    subscription: Mapped[Plan | None] = relationship(back_populates="subscribers", lazy='selectin')

    # Cloudpayments
    payment_token: Mapped[str | None] = mapped_column(sqlalchemy.String(32), default=None, unique=True)
    payment_method_string: Mapped[str | None] = mapped_column(sqlalchemy.String(32), default=None)
    pending_payment_id: Mapped[str | None] = mapped_column(sqlalchemy.String(32), default=None, unique=True)

    # Auto charge for extra plan
    extra_plan_autocharge: Mapped[bool] = mapped_column(default=True)
    
    preferences_id: Mapped[int] = mapped_column(sqlalchemy.ForeignKey("preferences.id"), default=0)

    # Active plans objects
    active_plans: Mapped[set[ActivePlan]] = relationship(back_populates="user", collection_class=set, lazy='selectin')
    # Secondary many-to-many relationship: plans which are present in active
    plans_in_use: Mapped[set[Plan]] = relationship(back_populates="users", secondary="active_plans", viewonly=True, lazy='selectin', collection_class=set)
    # Calls to this user
    calls: Mapped[set[Call]] = relationship(back_populates="user", collection_class=set, lazy='selectin')
    # SMS messages from/to this user
    sms: Mapped[set[SMS]] = relationship(back_populates="user", collection_class=set, lazy='selectin')
    # Sessions
    sessions: Mapped[set[AuthSession]] = relationship(back_populates="user", lazy='selectin', collection_class=set)
    # Saved telegram messages
    tg_messages: Mapped[set[TgMessage]] = relationship(back_populates="user", lazy='selectin', collection_class=set)
    # Scheduled actions
    scheduled_actions: Mapped[set[ScheduledAction]] = relationship(back_populates="user", lazy='selectin', collection_class=set)
    # Preferences (many to one)
    preferences: Mapped[Preferences] = relationship(lazy='selectin')
    # AmoCRM contacts
    amo_contact: Mapped[AmoContact | None] = relationship(back_populates="user", lazy='selectin')
    # Telegram conversation state
    state: Mapped[StateRecord] = relationship(back_populates="user", lazy='selectin')
    
    
    def get_pretty_name(self) -> str:
        return f"{self.first_name} {self.last_name}"


class Call(Base):
    __tablename__: typing.Final[str] = "calls"

    uid: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(sqlalchemy.ForeignKey("users.id"))
    timestamp: Mapped[datetime.datetime | None] = mapped_column(default=None)
    session_id: Mapped[str | None] = mapped_column(default=None)

    # TODO: Ensure the same format as in User!
    # TODO: Maybe remove this redundancy?
    callee_number: Mapped[str] = mapped_column(sqlalchemy.String(20))
    caller_number: Mapped[str] = mapped_column(sqlalchemy.String(20))
    recording_url: Mapped[str | None] = mapped_column(default=None)
    tg_message_id: Mapped[int | None] = mapped_column(sqlalchemy.BigInteger)
    finished: Mapped[bool] = mapped_column(default=False)

    extra_data: Mapped[dict] = mapped_column(sqlalchemy.JSON, default={})

    user: Mapped[User] = relationship(back_populates="calls", lazy='selectin')
    commands: Mapped[set[Command]] = relationship(back_populates="call", collection_class=set, lazy='selectin')


class Command(Base):
    __tablename__: typing.Final[str] = "commands"

    uid: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    call_uid: Mapped[uuid.UUID] = mapped_column(sqlalchemy.ForeignKey("calls.uid"))
    timestamp: Mapped[datetime.datetime] = mapped_column()
    command_name: Mapped[str] = mapped_column(sqlalchemy.String(16))
    contents: Mapped[dict] = mapped_column(sqlalchemy.JSON)

    call: Mapped[Call] = relationship(back_populates="commands", lazy='selectin')


class TgMessage(Base):
    __tablename__: typing.Final[str] = "tg_messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # None for unregistered users
    user_id: Mapped[int | None] = mapped_column(sqlalchemy.ForeignKey("users.id"), default=None)
    # 64 bits is sufficient capacity, according to telegram docs
    tg_chat_id: Mapped[int] = mapped_column(sqlalchemy.BigInteger)
    tg_message_id: Mapped[int] = mapped_column(sqlalchemy.BigInteger)
    from_us: Mapped[bool] = mapped_column()
    # Uses an arbitrary format. For debug only, as of now
    # data["msg"] = message.to_python()
    data: Mapped[dict] = mapped_column(sqlalchemy.JSON, default={})
    
    user: Mapped[User | None] = relationship(back_populates="tg_messages", lazy='selectin')


class Plan(Base):
    __tablename__: typing.Final[str] = "plans"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(sqlalchemy.String(64))

    # Null means no price was set, zero means free
    price: Mapped[int | None] = mapped_column(default=None)

    # Amount of months included to subscription
    # Null means infinite subscription, must be zero for extra plans
    months: Mapped[int] = mapped_column(default=1)

    # Extra plan means a small packet that applied if main plan limits were spent before renewal date
    is_extra: Mapped[bool] = mapped_column(default=False)

    # Per month
    # Null means unlimited, zero means "not included"
    calls: Mapped[int | None] = mapped_column(default=0)
    messages: Mapped[int | None] = mapped_column(default=0)

    extra_data: Mapped[dict] = mapped_column(sqlalchemy.JSON, default={})

    # Included options
    options: Mapped[set[Option]] = relationship(back_populates="plans", secondary="plan_options", lazy='selectin', collection_class=set)
    # Active plan objects which
    active_plans: Mapped[set[ActivePlan]] = relationship(back_populates="plan", collection_class=set, lazy='selectin')
    # Secondary many-to-many relationship: which users have this plan in their active plans
    users: Mapped[set[User]] = relationship(back_populates="plans_in_use", secondary="active_plans", viewonly=True, lazy='selectin', collection_class=set)
    # Users who are subscribed to this plan
    subscribers: Mapped[set[User]] = relationship(back_populates="subscription", collection_class=set, lazy='selectin')


class ActivePlan(Base):
    __tablename__: typing.Final[str] = "active_plans"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    user_id: Mapped[int] = mapped_column(sqlalchemy.ForeignKey("users.id"))
    plan_id: Mapped[int] = mapped_column(sqlalchemy.ForeignKey("plans.id"))

    start: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.min)
    end: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.max)

    # Null means unlimited
    calls_left: Mapped[int | None] = mapped_column(default=0)
    messages_left: Mapped[int | None] = mapped_column(default=0)

    payment_id: Mapped[int | None] = mapped_column(default=None)
    extra_data: Mapped[dict] = mapped_column(sqlalchemy.JSON, default={})

    user: Mapped[User] = relationship(back_populates="active_plans", lazy='selectin')
    plan: Mapped[Plan] = relationship(back_populates="active_plans", lazy='selectin')


class Option(Base):
    __tablename__: typing.Final[str] = "options"
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(sqlalchemy.String(64))
    desc: Mapped[str | None] = mapped_column(sqlalchemy.Text, default=None)

    # Plans that have this option
    plans: Mapped[set[Plan]] = relationship(back_populates="options", secondary="plan_options", lazy='selectin', collection_class=set)

    extra_data: Mapped[dict] = mapped_column(sqlalchemy.JSON, default={})


class PlanOption(Base):
    __tablename__: typing.Final[str] = "plan_options"
    
    plan_id: Mapped[int] = mapped_column(sqlalchemy.ForeignKey("plans.id"), primary_key=True)
    option_id: Mapped[int] = mapped_column(sqlalchemy.ForeignKey("options.id"), primary_key=True)


class AuthRequest(Base):
    __tablename__: typing.Final[str] = "auth_requests"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    phone: Mapped[str] = mapped_column(sqlalchemy.String(20))

    # active | rejected
    status: Mapped[str] = mapped_column(sqlalchemy.String(10), default="active")
    fail_count: Mapped[int] = mapped_column(default=0)

    created_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.min)
    expires_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.min)


class AuthCode(Base):
    __tablename__: typing.Final[str] = "auth_codes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    phone: Mapped[str] = mapped_column(sqlalchemy.String(20))
    code: Mapped[str] = mapped_column(sqlalchemy.String(6))
    used: Mapped[bool] = mapped_column(default=False)

    # Expired by default for security reasons
    created_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.min)
    expires_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.min)


class AuthSession(Base):
    __tablename__: typing.Final[str] = "auth_sessions"

    token: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(sqlalchemy.ForeignKey("users.id"))
    device_id: Mapped[int] = mapped_column(sqlalchemy.ForeignKey("devices.id"))

    # Manual expiration
    expired: Mapped[bool] = mapped_column(default=False)

    # Expired by default for security reasons
    created_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.min)
    expires_at: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.min)

    user: Mapped[User] = relationship(back_populates="sessions", lazy='selectin')
    device: Mapped[Device] = relationship(back_populates="sessions", lazy='selectin')


class AuthBannedPhone(Base):
    __tablename__: typing.Final[str] = "auth_banned_phones"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    phone: Mapped[str] = mapped_column(sqlalchemy.String(20), primary_key=True)
    reason: Mapped[str | None] = mapped_column(sqlalchemy.Text, default=None)

    start: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.min)
    end: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.max)


class Device(Base):
    __tablename__: typing.Final[str] = "devices"
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    
    # No clue what this will be. I assume it will be generated by the client.
    # Hopefully 255 bytes is enough.
    # This will probably also be used as the onesignal player id.
    # TODO: Remove unique constraint?
    device_uuid: Mapped[uuid.UUID] = mapped_column(unique=True)
    # 0 = IOS, 1 = Android, https://documentation.onesignal.com/reference/add-a-device
    onesignal_device_type: Mapped[int] = mapped_column()
    
    extra_data: Mapped[dict] = mapped_column(sqlalchemy.JSON, default={})
    
    # The last session can be extracted from this, so can be the last user.
    sessions: Mapped[set[AuthSession]] = relationship(back_populates="device", collection_class=set, lazy='selectin')


class SMS(Base):
    __tablename__: typing.Final[str] = "sms"
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    
    # Since we aim to support both incoming and outgoing SMS, this may correspond to
    # either the sender (if NOT is_incoming) or the receiver (if is_incoming).
    user_id: Mapped[int | None] = mapped_column(sqlalchemy.ForeignKey("users.id"), default=None)
    is_incoming: Mapped[bool] = mapped_column()
    
    timestamp: Mapped[datetime.datetime | None] = mapped_column(default=None)
    
    # Note: No spaces, dashes, etc.. (?)
    from_phone: Mapped[str] = mapped_column(sqlalchemy.String(20))
    to_phone: Mapped[str] = mapped_column(sqlalchemy.String(20))
    
    text: Mapped[str] = mapped_column(sqlalchemy.Text)
    
    extra_data: Mapped[dict] = mapped_column(sqlalchemy.JSON, default={})

    user: Mapped[User | None] = relationship(back_populates="sms", lazy='selectin')


class ScheduledAction(Base):
    __tablename__: typing.Final[str] = "scheduled_actions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    user_id: Mapped[int | None] = mapped_column(sqlalchemy.ForeignKey("users.id"), default=None)

    # Must be done by default
    time: Mapped[datetime.datetime] = mapped_column(default=datetime.datetime.min)
    done: Mapped[bool] = mapped_column(default=False)
    # Action itself
    type: Mapped[str] = mapped_column(sqlalchemy.String(50))
    args: Mapped[dict] = mapped_column(sqlalchemy.JSON, default={})

    user: Mapped[User | None] = relationship(back_populates="scheduled_actions", lazy='selectin')


class Preferences(Base):
    __tablename__: typing.Final[str] = "preferences"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    parent_id: Mapped[int | None] = mapped_column(sqlalchemy.ForeignKey("preferences.id"), default=0)
    values_override: Mapped[dict] = mapped_column(sqlalchemy.JSON, default={})
    
    # Note: join_depth is required to eagerly load this recursive relationship.
    #       Since we're using an async driver, we must have eager loading.
    #       This, however, poses a problem than when preferences subclassing 
    #       gets too deep, things will start to break. Currently, the depth
    #       shouldn't exceed 2, so I've set it to 5 to be safe. But be wary.
    parent: Mapped[Preferences | None] = relationship(remote_side=[id], lazy='selectin', join_depth=5)
    
    def get_values(self) -> dict:
        if self.parent is None:
            # To force-copy
            return dict(self.values_override)
        
        return self.parent.get_values() | self.values_override


class AmoContact(Base, sqlalchemy.orm.MappedAsDataclass):
    __tablename__: typing.Final[str] = "amo_contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    busy_user_id: Mapped[int | None] = mapped_column(sqlalchemy.ForeignKey("users.id"),
                                                     default=None, index=True, unique=True)

    user: Mapped[User] = relationship(back_populates="amo_contact", lazy='selectin')
    amo_leads: Mapped[set[AmoLead]] = relationship(back_populates='amo_contact', collection_class=set, lazy='selectin')


class AmoLead(Base, sqlalchemy.orm.MappedAsDataclass):
    __tablename__: typing.Final[str] = "amo_leads"

    id: Mapped[int] = mapped_column(primary_key=True)
    status_id: Mapped[int] = mapped_column(default=56012626)
    contact_id: Mapped[int] = mapped_column(sqlalchemy.ForeignKey("amo_contacts.id"))

    amo_contact: Mapped[AmoContact] = relationship(back_populates='amo_leads', lazy='selectin')


class AmoTokens(Base, sqlalchemy.orm.MappedAsDataclass):
    __tablename__: typing.Final[str] = 'amo_tokens'
    id: Mapped[int] = mapped_column(primary_key=True)

    refresh_token: Mapped[str]
    access_token: Mapped[str]


class StateRecord(Base):
    __tablename__: typing.Final[str] = 'state_records'
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    user_id: Mapped[int] = mapped_column(sqlalchemy.ForeignKey("users.id"))
    chat_id: Mapped[str] = mapped_column(sqlalchemy.String(16))

    state: Mapped[State] = mapped_column(sqlalchemy.String(100))
    data: Mapped[dict] = mapped_column(sqlalchemy.JSON, default={})

    user: Mapped[User] = relationship(back_populates='state', lazy='selectin')


__all__ = [
    "Base",
    "User",
    "Call",
    "Command",
    "TgMessage",
    "Plan",
    "ActivePlan",
    "Option",
    "PlanOption",
    "AuthRequest",
    "AuthCode",
    "AuthSession",
    "AuthBannedPhone",
    "Device",
    "SMS",
    "ScheduledAction",
    "Preferences",
    "AmoLead",
    "AmoContact",
    "AmoTokens",
    "StateRecord",
]
