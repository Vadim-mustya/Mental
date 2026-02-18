import asyncio
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest

from app.services.access import is_pro
from app.services.ui_session import set_ui_message, get_ui_message
from app.ui.keyboards import pro_locked_keyboard, pro_menu_keyboard, main_menu_keyboard
from app.services.ai_provider import AIProvider
from app.storage.pro_scenario_store import (
    get_scenario, upsert_stage1, upsert_stage2, upsert_stage3
)
from aiogram.dispatcher.event.bases import SkipHandler


router = Router()

ai: AIProvider | None = None

# пока тест-режим: GPT не вызываем
DRY_RUN_NO_GPT = True

MAX_CUSTOM_CHARS = 1000
ASK_TO_SHORTEN_TO = 800

PREFIX = "pro_scn"

QUESTIONS = [
    "Мой возраст —",
    "Страна, где я живу —",
    "Семейное положение —",
    "Мои 3 главных интереса —",
    "Чем я зарабатываю на жизнь —",
    "Моя рутина в жизни —",
    "Моя самая большая мечта —",
]

def _question_text(index: int) -> str:
    return f"{index + 1}) {QUESTIONS[index]}"


STATE: dict[int, dict] = {}


def scenario_menu_keyboard(has_stage1: bool) -> InlineKeyboardMarkup:
    # Нельзя сделать “неактивные” кнопки, поэтому показываем, но если нет stage1 — будем отвечать текстом
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Пройти тест (Этап 1)", callback_data=f"{PREFIX}:start")],
        [InlineKeyboardButton(text="2️⃣ События, если не выйти из сценария", callback_data=f"{PREFIX}:stage2")],
        [InlineKeyboardButton(text="3️⃣ День через 5 лет", callback_data=f"{PREFIX}:stage3")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="pro:menu"),
         InlineKeyboardButton(text="🏠 На главную", callback_data="pro:home")],
    ])


def back_home_keyboard(is_first_question: bool) -> InlineKeyboardMarkup:
    back_target = "pro:scenario" if is_first_question else f"{PREFIX}:back"
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data=back_target),
            InlineKeyboardButton(text="🏠 На главную", callback_data="pro:home"),
        ]
    ])



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


async def _force_new_ui(message: Message, tg_id: int, text: str, reply_markup=None):
    sent = await message.answer(text, reply_markup=reply_markup)
    set_ui_message(tg_id, sent.chat.id, sent.message_id)


def _init_user(tg_id: int):
    STATE[tg_id] = {"q": 0, "answers": {}}


def _build_stage1_prompt(answers: dict[int, str]) -> str:
    # Собираем финальный промт целиком как ты описал + добавляем маркеры для парсинга full/summary
    # Важно: просим модель ответить строго в формате, чтобы можно было разделить и сохранить.
    parts = [
        "Представь, что ты опытный транзактный аналитик, психолог с 30-летним стажем и умеешь прогнозировать будущее человека, "
        "учитывая его жизненный сценарий, условия экономики и политики государств, в котором живет человек, "
        "и учитываешь возраст и силу сопротивления к изменениям, относительно сценария, по которому живет человек.\n\n"
        "Проанализируй следующие данные обо мне:\n"
    ]

    for i, q in enumerate(QUESTIONS):
        a = answers.get(i, "").strip()
        parts.append(f"{q} {a}\n")

    parts.append(
        "\nНа основе этих данных создай детальное описание.\n\n"
        "Требования к результату:\n"
        "– 2000–2500 слов\n"
        "– Без рекомендаций\n"
        "– Без советов\n"
        "– Без клинических диагнозов\n"
        "– Без ссылок на теории и модели\n\n"
        "В конце не добавляй выводов, рекомендаций или предложений. Только описание.\n\n"
        "Также после описания напиши его короткую выжимку на 200-300 слов.\n\n"
        "Ответ выдай строго в формате:\n"
        "===FULL===\n"
        "<полное описание>\n"
        "===SUMMARY===\n"
        "<короткая выжимка>\n"
    )
    return "".join(parts)


def _stage2_prompt() -> str:
    return (
        "Теперь покажи три события, которые меня ждут, если я не выйду из жизненного сценария, "
        "согласно транзактного анализа, какие у них будут последствия, и как они отразятся на мне и моем здоровье.\n\n"
        "Ответ выдай строго в формате:\n"
        "===STAGE2===\n"
        "<текст>\n"
    )


def _stage3_prompt() -> str:
    return (
        "А теперь опиши один день из моей жизни через 5 лет, включая детали, о которых я сейчас даже не задумываюсь:\n"
        "Мои привычки —\n"
        "Образ мышления —\n"
        "С кем я живу —\n"
        "Как выгляжу —\n"
        "Как я себя чувствую —\n\n"
        "Ответ выдай строго в формате:\n"
        "===STAGE3===\n"
        "<текст>\n"
    )


def _parse_between(text: str, a: str, b: str) -> str:
    if a not in text:
        return ""
    after = text.split(a, 1)[1]
    if b in after:
        return after.split(b, 1)[0].strip()
    return after.strip()


async def _send_long(message: Message, text: str, chunk: int = 3500):
    # Telegram лимит ~4096, берём запас
    t = text.strip()
    while t:
        await message.answer(t[:chunk])
        t = t[chunk:]


@router.callback_query(F.data == "pro:scenario")
async def scenario_entry(cb: CallbackQuery):
    await _safe_answer(cb)
    tg_id = cb.from_user.id
    set_ui_message(tg_id, cb.message.chat.id, cb.message.message_id)

    if not is_pro(tg_id):
        await _render_ui(
            cb.message,
            tg_id,
            "Эта функция доступна только в ⭐ PRO.",
            reply_markup=pro_locked_keyboard()
        )
        return

    saved = await get_scenario(tg_id)
    has_stage1 = bool(saved and saved.get("stage1", {}).get("analysis_full"))

    text = (
        "🧩 Сценарный анализ жизни\n\n"
        "Описание (заглушка): тест состоит из 3 этапов.\n"
        "1) Сценарный анализ по вашим данным\n"
        "2) Прогноз событий, если не выйти из сценария\n"
        "3) Прогноз: один день через 5 лет\n\n"
        "Ниже выберите действие:"
    )

    await _render_ui(cb.message, tg_id, text, reply_markup=scenario_menu_keyboard(has_stage1))


@router.callback_query(F.data == f"{PREFIX}:start")
async def start_test(cb: CallbackQuery):
    await _safe_answer(cb)
    tg_id = cb.from_user.id
    set_ui_message(tg_id, cb.message.chat.id, cb.message.message_id)

    if not is_pro(tg_id):
        await _render_ui(cb.message, tg_id, "Эта функция доступна только в ⭐ PRO.", reply_markup=pro_locked_keyboard())
        return

    _init_user(tg_id)
    await _render_ui(
    cb.message,
    tg_id,
    f"{_question_text(0)}\n\nНапишите ответ (до {MAX_CUSTOM_CHARS} символов).",
    reply_markup=back_home_keyboard(is_first_question=True)
)



@router.callback_query(F.data == f"{PREFIX}:back")
async def back(cb: CallbackQuery):
    await _safe_answer(cb)
    tg_id = cb.from_user.id
    set_ui_message(tg_id, cb.message.chat.id, cb.message.message_id)

    if tg_id not in STATE:
        # нет активного теста — просто вернём в экран функции
        saved = await get_scenario(tg_id)
        has_stage1 = bool(saved and saved.get("stage1", {}).get("analysis_full"))
        await _render_ui(cb.message, tg_id, "🧩 Сценарный анализ жизни\n\nВыберите действие:", reply_markup=scenario_menu_keyboard(has_stage1))
        return

    st = STATE[tg_id]

    # если уже на первом вопросе — "Назад" возвращает в меню блока
    if st["q"] <= 0:
        saved = await get_scenario(tg_id)
        has_stage1 = bool(saved and saved.get("stage1", {}).get("analysis_full"))
        await _render_ui(
            cb.message,
            tg_id,
            "🧩 Сценарный анализ жизни\n\nВыберите действие:",
            reply_markup=scenario_menu_keyboard(has_stage1)
        )
        return

    st["q"] -= 1
    q = st["q"]

    await _render_ui(
        cb.message,
        tg_id,
        f"{_question_text(q)}\n\nНапишите ответ (до {MAX_CUSTOM_CHARS} символов).",
        reply_markup=back_home_keyboard(is_first_question=(q == 0))
    )



@router.message(F.text)
async def handle_text(message: Message):
    tg_id = message.from_user.id
    if tg_id not in STATE:
        raise SkipHandler

    text = (message.text or "").strip()
    if not text:
        await message.answer("Ответ пустой. Напишите хотя бы пару слов 🙂")
        return

    if len(text) > MAX_CUSTOM_CHARS:
        await message.answer(
            f"Ответ слишком длинный (>{MAX_CUSTOM_CHARS} символов).\n"
            f"Пожалуйста, сократите до {ASK_TO_SHORTEN_TO} символов."
        )
        return

    st = STATE[tg_id]
    q = st["q"]
    st["answers"][q] = text
    st["q"] += 1

    if st["q"] >= len(QUESTIONS):
        # финал теста
        await _force_new_ui(message, tg_id, "Готово ✅\n\nСобираю запрос для GPT…")
        asyncio.create_task(_finish_stage1(message, tg_id))
        return

    nq = st["q"]
    await _force_new_ui(
    message,
    tg_id,
    f"{_question_text(nq)}\n\nНапишите ответ (до {MAX_CUSTOM_CHARS} символов).",
    reply_markup=back_home_keyboard(is_first_question=(nq == 0))
)



async def _finish_stage1(message: Message, tg_id: int):
    st = STATE.get(tg_id)
    if not st:
        return

    answers = st["answers"]

    # сохраняем вопросы+ответы (как ты просил — вместе)
    qa = [{"q": QUESTIONS[i], "a": answers.get(i, "")} for i in range(len(QUESTIONS))]

    prompt = _build_stage1_prompt(answers)

    # пока GPT не вызываем — только подтверждение + сохранение Q/A
    if DRY_RUN_NO_GPT:
        await upsert_stage1(tg_id=tg_id, qa=qa, analysis_full=None, analysis_short=None)

        await message.answer(
            "✅ Этап 1 завершён (тестовый режим).\n\n"
            "Запрос успешно сформирован и готов к отправке в GPT.\n"
            "Сейчас GPT не вызываем, чтобы не тратить деньги."
        )

        # покажем промт (обрежем если слишком длинно для Telegram)
        payload = f"FINAL PROMPT:\n\n{prompt}"
        if len(payload) > 3800:
            payload = payload[:3800] + "\n\n…(обрезано для Telegram)"
        await message.answer(payload)

        # вернём пользователя в экран функции
        saved = await get_scenario(tg_id)
        has_stage1 = bool(saved and saved.get("stage1", {}).get("analysis_full"))
        await message.answer("🧩 Сценарный анализ жизни\n\nВыберите действие:", reply_markup=scenario_menu_keyboard(has_stage1))
        STATE.pop(tg_id, None)
        return

    # --- Боевой режим (позже включим) ---
    if not ai:
        await message.answer("AI не настроен.")
        STATE.pop(tg_id, None)
        return

    # 1) stage1 request
    resp1 = await ai.generate(system=_build_stage1_prompt(answers), user="")
    full = _parse_between(resp1, "===FULL===", "===SUMMARY===")
    summary = _parse_between(resp1, "===SUMMARY===", "")

    await upsert_stage1(tg_id=tg_id, qa=qa, analysis_full=full, analysis_short=summary)

    # покажем full пользователю
    await _send_long(message, full)

    # вернём в экран функции
    await message.answer("🧩 Сценарный анализ жизни\n\nВыберите действие:", reply_markup=scenario_menu_keyboard(True))
    STATE.pop(tg_id, None)


@router.callback_query(F.data == f"{PREFIX}:stage2")
async def stage2(cb: CallbackQuery):
    await _safe_answer(cb)
    tg_id = cb.from_user.id
    set_ui_message(tg_id, cb.message.chat.id, cb.message.message_id)

    if not is_pro(tg_id):
        await _render_ui(cb.message, tg_id, "Эта функция доступна только в ⭐ PRO.", reply_markup=pro_locked_keyboard())
        return

    saved = await get_scenario(tg_id)
    stage1_full = saved and saved.get("stage1", {}).get("analysis_full")

    if not stage1_full:
        await cb.message.answer("Сначала нужно пройти этап 1 (сценарный анализ).")
        return

    # если уже считали stage2 — просто отдадим
    s2 = saved.get("stage2", {}).get("text") if saved else None
    if s2:
        await _send_long(cb.message, s2)
        return

    if DRY_RUN_NO_GPT:
        await cb.message.answer("Этап 2 пока в тестовом режиме (GPT не вызываем).")
        return

    if not ai:
        await cb.message.answer("AI не настроен.")
        return

    # Боевой режим: запрос 2 (контекст = stage1_full)
    system = "Контекст:\n" + stage1_full
    user = _stage2_prompt()
    resp2 = await ai.generate(system=system, user=user)
    text = _parse_between(resp2, "===STAGE2===", "")
    await upsert_stage2(tg_id, text)
    await _send_long(cb.message, text)


@router.callback_query(F.data == f"{PREFIX}:stage3")
async def stage3(cb: CallbackQuery):
    await _safe_answer(cb)
    tg_id = cb.from_user.id
    set_ui_message(tg_id, cb.message.chat.id, cb.message.message_id)

    if not is_pro(tg_id):
        await _render_ui(cb.message, tg_id, "Эта функция доступна только в ⭐ PRO.", reply_markup=pro_locked_keyboard())
        return

    saved = await get_scenario(tg_id)
    stage1_full = saved and saved.get("stage1", {}).get("analysis_full")

    if not stage1_full:
        await cb.message.answer("Сначала нужно пройти этап 1 (сценарный анализ).")
        return

    s3 = saved.get("stage3", {}).get("text") if saved else None
    if s3:
        await _send_long(cb.message, s3)
        return

    if DRY_RUN_NO_GPT:
        await cb.message.answer("Этап 3 пока в тестовом режиме (GPT не вызываем).")
        return

    if not ai:
        await cb.message.answer("AI не настроен.")
        return

    system = "Контекст:\n" + stage1_full
    user = _stage3_prompt()
    resp3 = await ai.generate(system=system, user=user)
    text = _parse_between(resp3, "===STAGE3===", "")
    await upsert_stage3(tg_id, text)
    await _send_long(cb.message, text)
