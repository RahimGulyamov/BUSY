# from __future__ import annotations
# import typing
# import unittest
# import asyncio
# import sqlalchemy
# import sqlalchemy.orm
# import datetime
# import os
# from pathlib import Path
#
# from tests.utils import *
# import app.db as db
#
# class TestDbInterface(unittest.IsolatedAsyncioTestCase):
#     def tearDown(self) -> None:
#         os.remove("_test.db")
#
#     async def asyncSetUp(self) -> None:
#         await db.DatabaseApi().create_tables()
#
#         await db.DatabaseApi().put_user(db.User(
#             own_phone="+79990000001",
#             given_phone="+78880000001"
#         ))
#
#         await db.DatabaseApi().put_user(db.User(
#             own_phone="+79990000002",
#             given_phone="+78880000001"
#         ))
#
#         await db.DatabaseApi().put_user(db.User(
#             own_phone="+79990000003",
#             given_phone="+78880000002"
#         ))
#
#     async def asyncTearDown(self) -> None:
#         await db.DatabaseApi().engine.dispose()
#
#     async def test_find_by_own_phone(self) -> None:
#         user1: db.User | None = await db.DatabaseApi().find_user(own_phone="+79990000001")
#
#         self.assertIsNotNone(user1)
#         self.assertEqual(user1.own_phone, "+79990000001")
#         self.assertEqual(user1.given_phone, "+78880000001")
#
#         user2: db.User | None = await db.DatabaseApi().find_user(own_phone="+79990000002")
#
#         self.assertIsNotNone(user2)
#         self.assertEqual(user2.own_phone, "+79990000002")
#         self.assertEqual(user2.given_phone, "+78880000001")
#
#         user3: db.User | None = await db.DatabaseApi().find_user(own_phone="+79990000003")
#
#         self.assertIsNotNone(user3)
#         self.assertEqual(user3.own_phone, "+79990000003")
#         self.assertEqual(user3.given_phone, "+78880000002")
#
#         user4: db.User | None = await db.DatabaseApi().find_user(own_phone="+79990000004")
#
#         self.assertIsNone(user4)
#
#     async def test_find_by_given_phone(self) -> None:
#         user1: db.User | None = await db.DatabaseApi().find_user(given_phone="+78880000001")
#
#         self.assertIsNotNone(user1)
#         self.assertEqual(user1.own_phone, "+79990000002")
#         self.assertEqual(user1.given_phone, "+78880000001")
#
#         user2: db.User | None = await db.DatabaseApi().find_user(given_phone="+78880000002")
#
#         self.assertIsNotNone(user2)
#         self.assertEqual(user2.own_phone, "+79990000003")
#         self.assertEqual(user2.given_phone, "+78880000002")
#
#         user3: db.User | None = await db.DatabaseApi().find_user(given_phone="+78880000003")
#
#         self.assertIsNone(user3)
#
#     async def test_find_by_both_phones(self) -> None:
#         with self.assertRaises(Exception):
#             await db.DatabaseApi().find_user(own_phone="+79990000001", given_phone="+78880000001")
#
#     async def test_find_by_none(self) -> None:
#         with self.assertRaises(Exception):
#             await db.DatabaseApi().find_user()
#
#     async def test_put(self) -> None:
#         new_user = db.User(
#             own_phone="+79990000005",
#             given_phone="+78880000005"
#         )
#
#         await db.DatabaseApi().put_user(new_user)
#
#         user = await db.DatabaseApi().find_user(own_phone="+79990000005")
#
#         self.assertIsNotNone(user)
#         self.assertEqual(user.own_phone, new_user.own_phone)
#         self.assertEqual(user.given_phone, new_user.given_phone)