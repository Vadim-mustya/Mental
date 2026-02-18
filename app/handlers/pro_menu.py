from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.exceptions import TelegramBadRequest

from app.ui.keyboards import main_menu_keyboard, pro_menu_keyboard, pro_locked_keyboard
from app.services.access import is_pro
from app.services.ui_session import set_ui_message, get_ui_message

router = Router()


async def _safe_answer(cb: CallbackQuery):
    try:
        await cb.answer()
    except TelegramBadRequest:
        pass


async def _render_ui(message: Message, tg_id: int, text: str, reply_markup=None):
    ui = get_ui_message(tg_id)

    if not ui:
        sent = await message.answer(text, reply_markup=reply_markup)
        set_ui_message(tg_id, sent.chat.id, sent.message_id)
        return

    chat_id, ui_msg_id = ui

    if message.message_id and message.message_id > ui_msg_id:
        sent = await message.answer(text, reply_markup=reply_markup)
        set_ui_message(tg_id, sent.chat.id, sent.message_id)
        return

    try:
        await message.bot.edit_message_text(
            chat_id=chat_id,
            message_id=ui_msg_id,
            text=text,
            reply_markup=reply_markup
        )
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            return
        raise


@router.callback_query(F.data == "pro:home")
async def pro_home(cb: CallbackQuery):
    await _safe_answer(cb)
    tg_id = cb.from_user.id
    set_ui_message(tg_id, cb.message.chat.id, cb.message.message_id)

    await _render_ui(
        cb.message,
        tg_id,
        "Главное меню 👇",
        reply_markup=main_menu_keyboard()
    )


@router.callback_query(F.data == "pro:menu")
async def pro_menu(cb: CallbackQuery):
    await _safe_answer(cb)
    tg_id = cb.from_user.id
    set_ui_message(tg_id, cb.message.chat.id, cb.message.message_id)

    if not is_pro(tg_id):
        await _render_ui(
            cb.message,
            tg_id,
            "⭐ PRO раздел\n\nЧтобы открыть PRO-функции, нужна подписка.\n"
            "Пока оплаты нет — это заглушка доступа.",
            reply_markup=pro_locked_keyboard()
        )
        return

    await _render_ui(
        cb.message,
        tg_id,
        "⭐ PRO функции\n\nВыбери, что запустить 👇",
        reply_markup=pro_menu_keyboard()
    )


@router.callback_query(F.data == "pro:buy")
async def pro_buy(cb: CallbackQuery):
    await _safe_answer(cb)
    tg_id = cb.from_user.id
    set_ui_message(tg_id, cb.message.chat.id, cb.message.message_id)

    await _render_ui(
        cb.message,
        tg_id,
        "Оплата/подписка будет подключена позже.\n\n"
        "Сейчас мы разрабатываем функционал PRO.",
        reply_markup=pro_locked_keyboard()
    )
