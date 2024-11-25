from __future__ import annotations
import typing
import logging
import asyncio
import contextlib
import pathlib
import io
import aiohttp.client
import warnings
import boto3
import boto3.session
import boto3.resources.base
import botocore.client
from os import urandom

from ..common.singleton import Singleton


class CloudStorageAPI(Singleton):
    ENDPOINT_URL: typing.Final[str] = "https://storage.yandexcloud.net"
    BUCKET_NAME: typing.Final[str] = "busybucket"
    
    # boto3 is absolutely horrible in terms of typing...
    # I'm pretty sure there isn't even a concrete class for the things below.
    # Rather, they just inject attributes into an object of the base class.
    # God, I hate people like that.
    _session: boto3.session.Session
    _resource: boto3.resources.base.ServiceResource
    _bucket: typing.Any
    
    def __init__(self):
        import config
        
        if None in [config.AWS_ACCESS_KEY_ID, config.AWS_SECRET_ACCESS_KEY]:
            raise RuntimeError("No AWS credentials provided. Stopping.")
        
        self._session = boto3.session.Session(
            aws_access_key_id=config.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=config.AWS_SECRET_ACCESS_KEY,
        )
        
        self._resource = self._session.resource(
            "s3",
            endpoint_url=self.ENDPOINT_URL,
        )
        
        self._bucket = self._resource.Bucket(self.BUCKET_NAME)
    
    @property
    def _client(self) -> botocore.client.BaseClient:
        return self._resource.meta.client
    
    def protect_key(
        self,
        key: str | pathlib.PurePath,
    ) -> pathlib.PurePath:
        """
        Adds a random suffix to the key to prevent guessing. Useful for security against guessing.
        """
        
        if isinstance(key, str):
            key = pathlib.PurePath(key)
        assert isinstance(key, pathlib.PurePath)
        
        return key.parent / f"{key.stem}-{urandom(8).hex().upper()}{key.suffix}"
    
    async def upload(
        self,
        key: str | pathlib.PurePath,
        data: str | bytes | io.IOBase,
    ) -> pathlib.PurePath:
        """
        Uploads a file to the cloud storage.
        
        If `publish` is True, the file will be publicly available, but the key will be protected.
        """
        
        if isinstance(key, str):
            key = pathlib.PurePath(key)
        assert isinstance(key, pathlib.PurePath)
        
        if isinstance(data, str):
            data = data.encode()
        if isinstance(data, bytes):
            data = io.BytesIO(data)
        assert isinstance(data, io.IOBase)
        
        await asyncio.to_thread(self._bucket.upload_fileobj, data, key.as_posix())
    
    async def download(
        self,
        key: str | pathlib.PurePath,
        buffer: io.IOBase,
    ) -> None:
        """
        Downloads a file from the cloud storage.
        """
        
        if isinstance(key, str):
            key = pathlib.PurePath(key)
        assert isinstance(key, pathlib.PurePath)
        
        assert isinstance(buffer, io.IOBase)
        
        await asyncio.to_thread(self._bucket.download_fileobj, key.as_posix(), buffer)
    
    async def download_bytes(
        self,
        key: str | pathlib.PurePath,
    ) -> bytes:
        buffer = io.BytesIO()
        await self.download(key, buffer)
        return buffer.getvalue()
    
    async def upload_from_url(
        self,
        key: str | pathlib.PurePath,
        url: str,
        **kwargs: typing.Any,
    ) -> None:
        async with (
            aiohttp.client.ClientSession(**kwargs) as session,
            session.get(url) as response,
        ):
            # TODO: Stream the data instead of reading it all into memory?
            data: bytes = await response.read()
        
        await self.upload(key, data)
    
    async def publish_url(
        self,
        key: str | pathlib.PurePath,
    ) -> str:
        if isinstance(key, str):
            key = pathlib.PurePath(key)
        assert isinstance(key, pathlib.PurePath)
        
        self._resource.ObjectAcl(self.BUCKET_NAME, key.as_posix()).put(ACL='public-read')
        
        return f"{self.ENDPOINT_URL}/{self.BUCKET_NAME}/{key.as_posix()}"

    async def secure_upload_publish(
        self,
        key_base: str | pathlib.PurePath,
        *,
        data: str | bytes | io.IOBase | None = None,
        url: str | None = None,
    ) -> str:
        """
        Combines protect_key, upload (or upload_from_url), and publish_url.
        
        Creates a random suffix for the key, uploads the data,
        and returns the url with the public-read ACL.
        """
        
        key: pathlib.PurePath = self.protect_key(key_base)
        
        assert sum(map(lambda x: x is None, [data, url])) == 1, "Exactly one of data and url must be provided."
        
        if data is not None:
            await self.upload(key, data)
        else:
            await self.upload_from_url(key, url)
        
        return await self.publish_url(key)


__all__ = [
    "CloudStorageAPI",
]
