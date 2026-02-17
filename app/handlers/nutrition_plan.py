import asyncio
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.exceptions import TelegramBadRequest

from app.ui.keyboards import start_keyboard
from app.storage.users_store import can_use_free_nutrition, consume_free_nutrition_use
from app.services.ai_provider import AIProvider
from app.services.ui_session import set_ui_message, get_ui_message

router = Router()
ai: AIProvider | None = None

STATE_NUT: dict[int, dict] = {}

CAL_OPTIONS = [
    "1400–1600 ккал",
    "1600–1800 ккал",
    "1800–2000 ккал",
    "2000–2200 ккал",
    "✍ Свой вариант",
]

FORMAT_OPTIONS = [
    "Быстро и без сложной готовки",
    "Есть время на готовку",
    "Сразу готовая еда",
    "✍ Свой вариант",
]


def _kb(prefix: str, items: list[str]):
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    rows = []
    for idx, text in enumerate(items):
        rows.append([InlineKeyboardButton(text=text, callback_data=f"{prefix}{idx}")])
    rows.append([InlineKeyboardButton(text="🏠 На главную", callback_data="nut:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _safe_answer_callback(cb: CallbackQuery):
    try:
        await cb.answer()
    except TelegramBadRequest:
        pass


async def _render_ui(message: Message, tg_id: int, text: str, reply_markup=None, parse_mode: str | None = None):
    """
    UI всегда должен быть последним сообщением:
    - если UI последнее -> редактируем
    - если после UI были сообщения -> создаём новое UI
    """
    ui = get_ui_message(tg_id)

    if not ui:
        sent = await message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
        set_ui_message(tg_id, sent.chat.id, sent.message_id)
        return

    chat_id, ui_msg_id = ui

    if message.message_id and message.message_id > ui_msg_id:
        sent = await message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
        set_ui_message(tg_id, sent.chat.id, sent.message_id)
        return

    try:
        await message.bot.edit_message_text(
            chat_id=chat_id,
            message_id=ui_msg_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            return
        raise


async def _force_new_ui(message: Message, tg_id: int, text: str, reply_markup=None, parse_mode: str | None = None):
    sent = await message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
    set_ui_message(tg_id, sent.chat.id, sent.message_id)


async def _format_nutrition_report(text: str) -> str:
    divider = "━━━━━━━━━━━━━━━━━━━━"
    blocks = [b.strip() for b in text.split("\n\n") if b.strip()]
    if not blocks:
        return text
    first = blocks[0]
    if not first.startswith("**"):
        blocks[0] = f"**{first}**"
    return f"\n\n{divider}\n\n".join(blocks)


@router.callback_query(F.data == "nut:home")
async def nut_home(cb: CallbackQuery):
    await _safe_answer_callback(cb)
    tg_id = cb.from_user.id
    set_ui_message(tg_id, cb.message.chat.id, cb.message.message_id)

    STATE_NUT.pop(tg_id, None)
    await _render_ui(cb.message, tg_id, "Выбери действие 👇", reply_markup=start_keyboard())


@router.callback_query(F.data == "nut:start")
async def nut_start(cb: CallbackQuery):
    await _safe_answer_callback(cb)
    tg_id = cb.from_user.id

    set_ui_message(tg_id, cb.message.chat.id, cb.message.message_id)

    ok, msg = await can_use_free_nutrition(tg_id, limit_per_week=3)
    if not ok:
        await _render_ui(cb.message, tg_id, msg, reply_markup=start_keyboard(), parse_mode="Markdown")
        return

    STATE_NUT[tg_id] = {
        "step": "calories",
        "calories": None,
        "format": None,
        "awaiting_custom": None,  # "calories" or "format"
        "consumed": False,
    }

    await _render_ui(cb.message, tg_id, "Выбери примерную калорийность:", reply_markup=_kb("nut:cal:", CAL_OPTIONS))


@router.callback_query(F.data.startswith("nut:cal:"))
async def nut_pick_cal(cb: CallbackQuery):
    await _safe_answer_callback(cb)
    tg_id = cb.from_user.id
    set_ui_message(tg_id, cb.message.chat.id, cb.message.message_id)

    st = STATE_NUT.get(tg_id)
    if not st:
        return

    idx = int(cb.data.split(":")[-1])
    choice = CAL_OPTIONS[idx]

    if "Свой вариант" in choice:
        st["awaiting_custom"] = "calories"
        await _render_ui(
            cb.message,
            tg_id,
            "✍ Напиши свой вариант калорийности одним сообщением (например: 1750–1850 ккал):",
            reply_markup=_kb("nut:noop:", ["🏠 На главную"]),
        )
        return

    st["calories"] = choice
    st["step"] = "format"
    await _render_ui(cb.message, tg_id, "Выбери формат питания:", reply_markup=_kb("nut:fmt:", FORMAT_OPTIONS))


@router.callback_query(F.data.startswith("nut:fmt:"))
async def nut_pick_format(cb: CallbackQuery):
    await _safe_answer_callback(cb)
    tg_id = cb.from_user.id
    set_ui_message(tg_id, cb.message.chat.id, cb.message.message_id)

    st = STATE_NUT.get(tg_id)
    if not st:
        return

    idx = int(cb.data.split(":")[-1])
    choice = FORMAT_OPTIONS[idx]

    if "Свой вариант" in choice:
        st["awaiting_custom"] = "format"
        await _render_ui(
            cb.message,
            tg_id,
            "✍ Напиши свой вариант формата питания одним сообщением (например: “ем в столовой на работе”):",
            reply_markup=_kb("nut:noop:", ["🏠 На главную"]),
        )
        return

    st["format"] = choice
    st["step"] = "done"

    await _render_ui(cb.message, tg_id, "Формирую пример рациона…", reply_markup=None)
    asyncio.create_task(_finish_nutrition(cb.message, tg_id))


# этот handler срабатывает ТОЛЬКО когда ждём custom
@router.message(F.text, lambda m: m.from_user.id in STATE_NUT and STATE_NUT[m.from_user.id].get("awaiting_custom"))
async def nut_custom_text(message: Message):
    tg_id = message.from_user.id
    st = STATE_NUT.get(tg_id)
    if not st:
        return

    awaiting = st.get("awaiting_custom")
    text = (message.text or "").strip()
    if not text:
        await message.answer("Напиши текстом 🙂")
        return

    if awaiting == "calories":
        st["calories"] = text
        st["awaiting_custom"] = None
        st["step"] = "format"

        # после сообщения пользователя делаем новую UI, чтобы она была последней
        await _render_ui(message, tg_id, "Выбери формат питания:", reply_markup=_kb("nut:fmt:", FORMAT_OPTIONS))
        return

    if awaiting == "format":
        st["format"] = text
        st["awaiting_custom"] = None
        st["step"] = "done"

        await _render_ui(message, tg_id, "Формирую пример рациона…", reply_markup=None)
        asyncio.create_task(_finish_nutrition(message, tg_id))
        return


async def _finish_nutrition(message: Message, tg_id: int):
    global ai
    if ai is None:
        await message.answer("AI не инициализирован. Проверь запуск main.py")
        return

    st = STATE_NUT.get(tg_id)
    if not st:
        return

    calories = st.get("calories") or "не указано"
    fmt = st.get("format") or "не указано"

    system_prompt = (
        "Твоя роль:\n"
        "Представь, что ты персональный AI-ассистент по питанию и образу жизни с 20-летним опытом работы с людьми. "
        "Ты умеешь подбирать понятные, реалистичные варианты рациона, которые выглядят практично и применимо в обычной жизни. "
        "Ты не врач и не нутрициолог, а помощник, который предлагает примеры и помогает упростить выбор еды на день.\n\n"
        "Твоя задача:\n"
        "На основе выбранной пользователем примерной калорийности и формата питания составить структурированный, реалистичный пример рациона на 1 день. "
        "Рацион должен быть понятным, несложным, разнообразным и легко читаемым в формате Telegram.\n\n"
        "Правила:\n"
        "Не давай медицинских рекомендаций и не используй формулировки, связанные с лечением, заболеваниями, противопоказаниями или терапией.\n"
        "Не рассчитывай индивидуальную норму калорий и не используй формулы БЖУ.\n"
        "Не указывай точные граммовки и строгие цифры — допускаются только примерные диапазоны калорий.\n"
        "Избегай категоричных утверждений и запугивающих формулировок.\n"
        "Не используй сложные или редкие ингредиенты.\n"
        "Рацион должен быть реалистичным для обычного человека, без экзотики и дорогих продуктов.\n"
        "Не повторяй один и тот же продукт во всех приёмах пищи.\n"
        "Пиши по-русски, дружелюбно и уверенно, без пафоса.\n"
        "Ответ должен быть структурированным и легко читаемым.\n"
        "Общий объём ответа — не более 700–900 слов.\n"
        "Если данных недостаточно — сделай разумные предположения, но не акцентируй на этом внимание.\n\n"
        "Структура ответа (строго):\n"
        "Заголовок:\n"
        "“Пример рациона на ~{калорийность}”\n"
        "Подзаголовок:\n"
        "(примерная калорийность, не строгий расчёт)\n"
        "🍳 Завтрак (~X–Y ккал)\n"
        " – 2–3 позиции\n"
        " 💡 Альтернатива: 1 вариант\n"
        "🍲 Обед (~X–Y ккал)\n"
        " – 2–3 позиции\n"
        " 💡 Альтернатива: 1 вариант\n"
        "🍎 Перекус (~X–Y ккал)\n"
        " – 1–2 позиции\n"
        "🍽 Ужин (~X–Y ккал)\n"
        " – 2–3 позиции\n"
        " 💡 Альтернатива: 1 вариант\n"
        "🔢 Итого: ~примерный диапазон ккал\n"
        "🔁 Можно заменить:\n"
        " – продукт → 2–3 варианта\n"
        " – продукт → 2–3 варианта\n"
        "Завершение:\n"
        "1 короткое поддерживающее предложение + мягкое предложение перейти в PRO-версию (без давления).\n"
        "Например: “Хочешь, я могу составить меню на неделю с учётом твоих предпочтений и графика?”\n"
    ).replace("{калорийность}", calories)

    user_text = (
        "Ответы пользователя:\n"
        f"Примерная калорийность: “{calories}”\n"
        f"Формат питания: “{fmt}”\n"
    )

    try:
        report = await ai.generate(system_prompt=system_prompt, user_text=user_text)
    except Exception as e:
        await _render_ui(message, tg_id, f"Не удалось получить рацион от AI.\n\n(Тех. причина: {e})", reply_markup=start_keyboard())
        return

    if not report or "пустой ответ" in report.lower():
        await _render_ui(message, tg_id, report or "AI вернул пустой ответ. Попробуй ещё раз.", reply_markup=start_keyboard())
        return

    # списываем попытку только после успешного ответа
    if not st.get("consumed"):
        ok, msg = await consume_free_nutrition_use(tg_id, limit_per_week=3)
        if not ok:
            await _render_ui(message, tg_id, msg, reply_markup=start_keyboard(), parse_mode="Markdown")
            STATE_NUT.pop(tg_id, None)
            return
        st["consumed"] = True

    report = await _format_nutrition_report(report)

    # результат отдельным сообщением (остаётся)
    await message.answer(report, parse_mode="Markdown")

    # UI делаем последним сообщением
    await _force_new_ui(message, tg_id, "Готово ✅\n\nВыбери действие 👇", reply_markup=start_keyboard())

    STATE_NUT.pop(tg_id, None)
