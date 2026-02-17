from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧠 Психологический портрет (Free)", callback_data="mental:start")],
        [InlineKeyboardButton(text="⭐ PRO функции", callback_data="pro:menu")],
    ])


def pro_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 PRO #1 (заглушка)", callback_data="pro:feature:one")],
        [InlineKeyboardButton(text="🏠 В главное меню", callback_data="pro:home")],
    ])


def pro_locked_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Как получить PRO (скоро)", callback_data="pro:buy")],
        [InlineKeyboardButton(text="🏠 В главное меню", callback_data="pro:home")],
    ])


def question_keyboard(prefix: str, q_index: int, options: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for opt in options:
        rows.append([InlineKeyboardButton(
            text=opt["text"],
            callback_data=f"{prefix}:ans:{q_index}:{opt['id']}"
        )])

    nav = []
    if q_index > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{prefix}:back"))
    nav.append(InlineKeyboardButton(text="🏠 В меню", callback_data=f"{prefix}:home"))
    rows.append(nav)

    return InlineKeyboardMarkup(inline_keyboard=rows)


def custom_keyboard(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{prefix}:back"),
            InlineKeyboardButton(text="🏠 В меню", callback_data=f"{prefix}:home"),
        ]
    ])
