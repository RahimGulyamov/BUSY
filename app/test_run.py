from __future__ import annotations
import typing
import sqlalchemy
import sqlalchemy.orm
import sqlalchemy.sql
import uuid

from . import common


async def test_run() -> None:
    raise NotImplementedError("Not in use currently")
    
    await common.onesignal.onesignal_send_push("Report in chat if you see this :)", None)
    await common.onesignal.onesignal_send_push("You shouldn't see this in any app!", uuid.UUID("00000000-0000-0000-0000-000000000001"))

__all__ = [
    "test_run",
]
