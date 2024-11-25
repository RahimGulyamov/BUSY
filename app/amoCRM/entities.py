import typing

pipeline_id: typing.Final[int] = 6589714
lead_first_contact_id: typing.Final[int] = 56012626
lead_bot_registry_id: typing.Final[int] = 56012630
lead_make_solution_id: typing.Final[int] = 56012634
lead_buying_tariff_id: typing.Final[int] = 56012638
lead_success_closed_id: typing.Final[int] = 142
telegram_field_id: typing.Final[int] = 933089
phone_field_id: typing.Final[int] = 269061


def get_contact_object(first_name: str | None, last_name: str | None, telegram: str | None) -> list:
    contact = [
        {
            "first_name": first_name or '',
            "last_name": last_name or '',
            "custom_fields_values": [
                {
                    "field_id": telegram_field_id,
                    "field_name": "Telegram",
                    "values": [
                        {
                            "value": telegram or '',
                        }
                    ]
                }
            ]
        }
    ]
    return contact


def get_new_contact_id(contact_info: dict) -> int:
    return contact_info.get('_embedded').get('contacts')[0].get('id')


def get_updating_lead_contact(contact_id: int, lead_id: int) -> list:
    contact = [
        {
            "id": contact_id,
            "_embedded": {
                "leads": [
                    {
                        "id": lead_id
                    }
                ]
            }
        }
    ]
    return contact


def get_updating_phone_contact(contact_id: int, phone: str) -> list:
    contact = [
        {
            "id": contact_id,
            "custom_fields_values": [
                {
                    "field_id": phone_field_id,
                    "field_name": "Телефон",
                    "values": [
                        {
                            "value": phone,
                        }
                    ]
                }
            ]
        }
    ]
    return contact


def get_lead_object(contact_id: int) -> list:
    deal = [
        {
            "status_id": lead_first_contact_id,
            "pipeline_id": pipeline_id,
            "_embedded": {
                "contacts": [
                    {
                        "id": contact_id
                    }
                ]
            }

        }
    ]
    return deal


def get_new_lead_id(lead_info: dict) -> int:
    return lead_info.get('_embedded').get('leads')[0].get('id')


def get_updating_lead_object(lead_id: int, status_id: int, pipeline: int = pipeline_id) -> list:
    new_lead = [
        {
            "id": lead_id,
            "pipeline_id": pipeline,
            "status_id": status_id
        }]
    return new_lead
