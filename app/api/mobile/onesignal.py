from __future__ import annotations
import typing
import logging
import uuid
from aiohttp import client
from sqlalchemy.ext.asyncio import AsyncSession

from ... import db


async def onesignal_register_device(device_type: int, device_uuid: uuid.UUID) -> None:
    """
    Registers a device with onesignal.
    """
    
    import config

    logging.info(f"Device registration request: device_type={device_type}, device_uuid={device_uuid}")

    if not config.ONESIGNAL_APP_ID:
        # It is important to later manually register all devices, for which the registration
        # has been skipped
        logging.warning(f"Onesignal is not configured, skipping device registration for {device_uuid}")
        return

    assert isinstance(device_type, int)
    assert isinstance(device_uuid, uuid.UUID)
    assert device_type in range(0, 15), "Unexpected onesignal device type"
    
    data: dict[str, typing.Any] = {
        "app_id": config.ONESIGNAL_APP_ID,
        "device_type": device_type,
        "external_user_id": str(device_uuid),
    }

    registration_status = False
    async with (
        client.ClientSession(headers={"Authorization": f"Basic {config.ONESIGNAL_REST_API_KEY}"}) as session,
        session.post("https://onesignal.com/api/v1/players", json=data) as response,
    ):
        if response.status != 200:
            logging.warning(f"Onesignal device {device_uuid} registration failed with {response.status}")
            logging.warning(await response.text())
        else:
            registration_status = True
            logging.info(f"Onesignal device {device_uuid} registered successfully")
            logging.info(await response.text())
    
    async with db.DatabaseApi().session():
        await db.DatabaseApi().change_device_registration_status(
            device_uuid=device_uuid, 
            status=registration_status
        )


async def onesignal_send_push(text: str, target_devices: uuid.UUID | list[uuid.UUID] | None) -> None:
    """
    Send a push notification to a device, a list of devices or all devices.
    
    Note that if a device is not registered with onesignal, this method will either
    produce a warning or fail silently, but in any case won't give any tangible feedback.
    """
    
    import config
    
    if not config.ONESIGNAL_APP_ID:
        logging.warning(f"Onesignal is not configured, skipping notification for {target_devices}")
        return
    
    assert isinstance(text, str)
    
    data: dict[str, typing.Any] = {
        "app_id": config.ONESIGNAL_APP_ID,
        "contents": {"en": text},
    }
    
    device_uuids = []
    if target_devices is None:
        # TODO: Add an All segment in the onesignal dashboard
        data["included_segments"] = ["All"]
    elif isinstance(target_devices, uuid.UUID):
        device_uuids.append(target_devices)
        async with db.DatabaseApi().session():
            device: db.model.Device = await db.DatabaseApi().get_device_info(device_uuid=target_devices)
        if not device.extra_data.setdefault("registered", False):
            await onesignal_register_device(device.onesignal_device_type, device.device_uuid)
        
        data["include_external_user_ids"] = [str(target_devices)]
    else:
        assert isinstance(target_devices, list)
        
        if not target_devices:
            logging.info(f"Onesignal push notification sending failed: no target devices")
            return
        
        for device_uuid in target_devices:
            device_uuids.append(device_uuid)
            async with db.DatabaseApi().session():
                device: db.model.Device = await db.DatabaseApi().get_device_info(device_uuid=device_uuid)
            if not device.extra_data.setdefault("registered", False):
                await onesignal_register_device(device.onesignal_device_type, device.device_uuid)

        data["include_external_user_ids"] = [str(device) for device in target_devices]

    logging.info(data)
    async with (
        client.ClientSession(headers={"Authorization": f"Basic {config.ONESIGNAL_REST_API_KEY}"}) as session,
        session.post("https://onesignal.com/api/v1/notifications", json=data) as response,
    ):
        if response.status != 200:
            logging.warning(f"Onesignal push notification sending failed with {response.status}")
            logging.warning(await response.text())
        else:
            response_json = await response.json()
            if response_json["id"] == "" or response_json["id"] is None:
                logging.info(f"Onesignal push notification sending failed with {response_json['errors']}")
                async with db.DatabaseApi().session():
                    for device_uuid in device_uuids:
                        await db.DatabaseApi().change_device_registration_status(
                            device_uuid=device_uuid, 
                            status=False
                            )
            else:
                logging.info(f"Onesignal push notification sent successfully")
                logging.info(await response.text())


__all__ = [
    "onesignal_register_device",
    "onesignal_send_push",
]
