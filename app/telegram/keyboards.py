from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, \
                          InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.callback_data import CallbackData
import typing

from ..common.assistant_config import ASSISTANT_VOICES

def kb_get_number() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup()
    b_get_number = KeyboardButton(text='Поделиться номером телефона!', request_contact=True)
    kb.add(b_get_number)
    return kb


def kb_welcome() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    b_choose_tariff = KeyboardButton(text="Выбрать тариф 💳")
    b_how_busy_works = KeyboardButton(text="Как работает Busy 🤔")
    b_support = KeyboardButton(text="Поддержка 👨‍💻")
    kb.add(b_choose_tariff, b_how_busy_works, b_support)
    return kb


def kb_main(with_number: bool) -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    b_call = KeyboardButton('Позвонить 📞')
    b_send_message = KeyboardButton('Отправить смс 📩')
    b_my_tariff = KeyboardButton('Мой тариф 💵')
    b_setting = KeyboardButton('Настройки ⚙️')
    b_support = KeyboardButton(text="Поддержка 👨‍💻")
    
    if with_number:
        kb.add(b_call, b_send_message).add(b_my_tariff).add(b_setting).add(b_support)
    else:
        kb.add(b_my_tariff, b_setting, b_support)
    
    return kb


def kb_main_without_number() -> ReplyKeyboardMarkup:
    return kb_main(with_number=False)


def kb_main_with_number() -> ReplyKeyboardMarkup:
    return kb_main(with_number=True)


def ikb_setting(with_number: bool, extra_autocharge: bool) -> InlineKeyboardMarkup:
    ikb_setting = InlineKeyboardMarkup(row_width=1)
    ib_change_tariff = InlineKeyboardButton(text="Сменить тариф 💳", callback_data="Change tariff")
    ib_payment_method = InlineKeyboardButton(text="Метод оплаты 💰", url='https://busy.contact')
    ib_connect_virtual_number = InlineKeyboardButton(text="Подключить виртуальный номер 📱",
                                                     callback_data="Connect a virtual number")
    ib_customize = InlineKeyboardButton(text="Кастомизация 🎨", callback_data="Customize")
    ib_turn_off = InlineKeyboardButton(text="Выключить Busy 😔", callback_data="Turn off Busy")

    ib_autocharge = InlineKeyboardButton(text="Выключить автопродление" if extra_autocharge else "Включить автопродление",
                                         callback_data="Toggle autocharge")

    if with_number:
        ikb_setting.add(
            ib_change_tariff, ib_payment_method, ib_autocharge, ib_customize, ib_turn_off,
        )
    else:
        ikb_setting.add(
            ib_change_tariff, ib_payment_method, ib_autocharge, ib_connect_virtual_number, ib_customize, ib_turn_off,
        )
    
    return ikb_setting


def ikb_voices() -> InlineKeyboardMarkup:
    buttons = []
    for voice in ASSISTANT_VOICES.values():
        buttons.append(InlineKeyboardButton(text=voice.name, callback_data=f"Voice {voice.id}"))

    ikb_setting = InlineKeyboardMarkup(row_width=1)
    ikb_setting.add(*buttons)
    ib_cancel = InlineKeyboardButton(text="Отмена", callback_data="Cancel")
    ikb_setting.add(ib_cancel)
    return ikb_setting


def ikb_tariff() -> InlineKeyboardMarkup:
    ikb = InlineKeyboardMarkup(row_width=1)
    ib_tariff1 = InlineKeyboardButton(text="Very Busy", callback_data="Tariff Very Busy")
    ib_tariff2 = InlineKeyboardButton(text="Super Busy", callback_data="Tariff Super Busy")
    ib_tariff3 = InlineKeyboardButton(text="Ultra Busy", callback_data="Tariff Ultra Busy")
    ib_tariff_info = InlineKeyboardButton(text='О тарифах 💳', callback_data='Help Tariff Info')
    ikb.add(ib_tariff1, ib_tariff2, ib_tariff3, ib_tariff_info)
    return ikb


def ikb_tariff_change_confirmation() -> InlineKeyboardMarkup:
    ikb = InlineKeyboardMarkup(row_width=2)
    ib_confirm = InlineKeyboardButton(text='Да', callback_data='Confirm')
    ib_back = InlineKeyboardButton(text='Haзад', callback_data='Back')
    ikb.add(ib_confirm, ib_back)
    return ikb


def ikb_recurrent_fail() -> InlineKeyboardMarkup:
    ikb = InlineKeyboardMarkup(row_width=2)
    ib_retry = InlineKeyboardButton(text='Попробовать еще раз', callback_data='Failed Recurrent Retry')
    ib_paymethod = InlineKeyboardButton(text='Сменить метод оплаты 💳', callback_data='Recurrent change payment method')
    ib_turn_off = InlineKeyboardButton(text="Выключить Busy 😔", callback_data="Turn off Busy")
    ikb.add(ib_retry, ib_paymethod, ib_turn_off)
    return ikb

def ikb_extra_fail() -> InlineKeyboardMarkup:
    ikb = InlineKeyboardMarkup(row_width=2)
    ib_retry = InlineKeyboardButton(text='Попробовать еще раз', callback_data='Failed Extra Retry')
    ib_paymethod = InlineKeyboardButton(text='Сменить метод оплаты 💳', callback_data='Extra change payment method')
    ib_turn_off = InlineKeyboardButton(text="Выключить Busy 😔", callback_data="Turn off Busy")
    ikb.add(ib_retry, ib_paymethod, ib_turn_off)
    return ikb


def ikb_tariff_change_confirmation_paymethod() -> InlineKeyboardMarkup:
    ikb = InlineKeyboardMarkup(row_width=2)
    ib_confirm = InlineKeyboardButton(text='Оплатить', callback_data='Confirm')
    ib_back = InlineKeyboardButton(text='Haзад', callback_data='Back')
    ib_paymethod = InlineKeyboardButton(text='Сменить метод оплаты 💳', callback_data='Change payment method')
    ikb.add(ib_confirm, ib_back, ib_paymethod)
    return ikb


def ikb_cancel_payment() -> InlineKeyboardMarkup:
    ikb = InlineKeyboardMarkup(row_width=2)
    b_cancel = KeyboardButton(text="Отменить", callback_data='CancelPayment')
    ikb.add(b_cancel)
    return ikb


def ikb_tariff_change_payment_failed() -> InlineKeyboardMarkup:
    ikb = InlineKeyboardMarkup(row_width=2)
    ib_confirm = InlineKeyboardButton(text='Попробовать еще раз', callback_data='Confirm')
    ib_back = InlineKeyboardButton(text='Отмена', callback_data='CancelPayment')
    ib_paymethod = InlineKeyboardButton(text='Сменить метод оплаты 💳', callback_data='Change payment method')
    ikb.add(ib_confirm, ib_back, ib_paymethod)
    return ikb


def ikb_my_tariff() -> InlineKeyboardMarkup:
    ikb = InlineKeyboardMarkup(row_width=1)
    ib_change_tariff = InlineKeyboardButton(text="Сменить тариф 💳", callback_data="Change tariff")
    ib_payment_method = InlineKeyboardButton(text="Метод оплаты 💰", url='https://busy.contact')
    ikb.add(ib_change_tariff, ib_payment_method)
    return ikb


def ikb_turn_off_busy() -> InlineKeyboardMarkup:
    ikb = InlineKeyboardMarkup(row_width=2)
    ib_yes = InlineKeyboardButton(text="Да", callback_data="Stop Busy true")
    ib_no = InlineKeyboardButton(text="Нет", callback_data="Stop Busy false")
    ikb.add(ib_yes, ib_no)
    return ikb


def ikb_incoming_call(with_write_answer: bool) -> InlineKeyboardMarkup:
    ikb = InlineKeyboardMarkup(row_width=2)
    ib_busy = InlineKeyboardButton(text="🤖 Занят", callback_data="I am busy")
    ib_call_back = InlineKeyboardButton(text="🤖 Перезвоню", callback_data="I am call back")
    ib_write_answer = InlineKeyboardButton(text="✏️ Ответить", callback_data="Write answer")
    ib_connect = InlineKeyboardButton(text="📞 Соединить", callback_data="Connect")
    
    if with_write_answer:
        ikb.add(ib_busy, ib_call_back, ib_write_answer, ib_connect)
    else:
        ikb.add(ib_busy, ib_call_back, ib_connect)
    return ikb


def ikb_incoming_call_with_write_answer() -> InlineKeyboardMarkup:
    return ikb_incoming_call(with_write_answer=True)


def ikb_incoming_call_without_write_answer() -> InlineKeyboardMarkup:
    return ikb_incoming_call(with_write_answer=False)


def ikb_call_back() -> InlineKeyboardMarkup:
    ikb = InlineKeyboardMarkup(row_width=2)
    ib_call_back = InlineKeyboardButton(text="📞 Перезвонить", callback_data="Callback after the end of call")
    ikb_send_message = InlineKeyboardButton(text="📩 Отправить смс", callback_data="Send message after the end of call")
    ikb.add(ib_call_back, ikb_send_message)
    return ikb


def kb_cancel() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    b_cancel = KeyboardButton(text="Отмена ◀️")
    kb.add(b_cancel)
    return kb


def ikb_confirm() -> InlineKeyboardMarkup:
    ikb = InlineKeyboardMarkup(row_width=2)
    ib_yes = InlineKeyboardButton(text='Да', callback_data='Yes')
    ib_change = InlineKeyboardButton(text='Изменить', callback_data='Change')
    ikb.add(ib_yes, ib_change)
    return ikb


def ikb_help() -> InlineKeyboardMarkup:
    ikb = InlineKeyboardMarkup(row_width=1)
    ib_info = InlineKeyboardButton(text='О сервисе Busy 🐝', callback_data='Help Info')
    ib_redirection_megafon = InlineKeyboardButton(text='Переадресация Мегафон 🟢',
                                                  callback_data='Help Redirection Megafon')
    ib_redirection_mts = InlineKeyboardButton(text='Переадресация МТС 🔴', callback_data='Help Redirection MTS')
    ib_redirection_beeline = InlineKeyboardButton(text='Переадресация Билайн 🟡',
                                                  callback_data='Help Redirection Beeline')
    ib_redirection_other = InlineKeyboardButton(text='Переадресация на других операторах ⚫️',
                                                callback_data='Help Redirection Other')
    ib_tariff_info = InlineKeyboardButton(text='О тарифах 💳', callback_data='Help Tariff Info')
    ikb.add(ib_info, ib_redirection_megafon, ib_redirection_mts, ib_redirection_beeline, ib_redirection_other,
            ib_tariff_info)
    return ikb


def ikb_customize(user_config: dict) -> InlineKeyboardMarkup:
    if isinstance(user_config["VOX_GREETING"], list):
        greeting = ', '.join([value["text"] for value in user_config["VOX_GREETING"]])
    else:
        greeting = user_config["VOX_GREETING"]["text"]
    voice_name = ASSISTANT_VOICES[user_config["VOX_VOICE"]].name
    name = user_config["USER_DISPLAY_NAME"]
    ignore_list_len = len(user_config["IGNORE_LIST"])
    chatgpt_available = user_config["CHATGPT_AVAILABLE"]
    chatgpt_enabled = user_config["CHATGPT_ENABLED"]
    # TODO: Consider between ✅❎🟩🟨🟥
    chatgpt_status = "✅" if chatgpt_enabled else "🟥"
    # chatgpt_instructions = user_config["CHATGPT_INSTRUCTIONS"]

    ikb = InlineKeyboardMarkup(row_width=1)
    ib_greeting = InlineKeyboardButton(text=f"Приветствие: {greeting}", callback_data="Change greeting")
    ib_voice = InlineKeyboardButton(text=f"Голос: {voice_name}", callback_data="Change voice")
    ib_name = InlineKeyboardButton(text=f"Обращение: {name}", callback_data="Change name")
    ib_ignorelist = InlineKeyboardButton(text=f"Игнорируемые номера: ({ignore_list_len} шт.)", callback_data="Change ignorelist")
    ib_chatgpt = InlineKeyboardButton(text=f"ChatGPT: {chatgpt_status}", callback_data="Change chatgpt")
    ib_chatgpt_instructions = InlineKeyboardButton(text=f"Инструкция для ChatGPT", callback_data="Change chatgpt instructions")
    ib_back = InlineKeyboardButton(text='Haзад', callback_data='Back')
    
    buttons = [ib_greeting, ib_voice, ib_name, ib_ignorelist]
    if chatgpt_available:
        buttons.append(ib_chatgpt)
        if chatgpt_enabled:
            buttons.append(ib_chatgpt_instructions)
    buttons.append(ib_back)
    ikb.add(*buttons)
    
    return ikb


cbd_ignorelist = CallbackData("ignorelist", "action", "number")


def ikb_cancel_customization() -> InlineKeyboardMarkup:
    ikb = InlineKeyboardMarkup(row_width=1)
    ib_cancel = InlineKeyboardButton(text="Отмена", callback_data="Cancel")
    ikb.add(ib_cancel)
    return ikb


def ikb_ignorelist(user_config: dict) -> InlineKeyboardMarkup:
    ignore_list = user_config["IGNORE_LIST"]
    ikb = InlineKeyboardMarkup(row_width=1)
    for number in ignore_list:
        ib_number = InlineKeyboardButton(text=f"Удалить {number}", callback_data=cbd_ignorelist.new(action="delete", number=number))
        ikb.add(ib_number)
    ib_add = InlineKeyboardButton(text=f"Добавить номер", callback_data=cbd_ignorelist.new(action="add", number=""))
    ib_back = InlineKeyboardButton(text='Haзад', callback_data=cbd_ignorelist.new(action="back", number=""))
    ikb.add(ib_add, ib_back)
    return ikb


__all__ = [
    "kb_welcome",
    "kb_main",
    "kb_main_without_number",
    "kb_main_with_number",
    "kb_get_number",
    "kb_cancel",
    "ikb_voices",
    "ikb_tariff",
    "ikb_cancel_payment",
    "ikb_tariff_change_confirmation",
    "ikb_recurrent_fail",
    "ikb_extra_fail",
    "ikb_tariff_change_confirmation_paymethod",
    "ikb_tariff_change_payment_failed",
    "ikb_my_tariff",
    "ikb_incoming_call",
    "ikb_incoming_call_with_write_answer",
    "ikb_incoming_call_without_write_answer",
    "ikb_setting",
    "ikb_turn_off_busy",
    "ikb_call_back",
    "ikb_confirm",
    "ikb_help",
    "ikb_customize",
    "ikb_cancel_customization",
    "cbd_ignorelist",
    "ikb_ignorelist",
]
