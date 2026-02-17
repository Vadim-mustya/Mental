from aiogram import Router
from aiogram.filters import CommandStart, Command
from aiogram.types import Message

from app.ui.keyboards import main_menu_keyboard
from app.services.ui_session import set_ui_message

router = Router()


@router.message(CommandStart())
@router.message(Command("start"))
async def cmd_start(message: Message):
    tg_id = message.from_user.id

    # Всегда создаём новое меню и делаем его "последним UI"
    sent = await message.answer("Главное меню 👇", reply_markup=main_menu_keyboard())
    set_ui_message(tg_id, sent.chat.id, sent.message_id)
