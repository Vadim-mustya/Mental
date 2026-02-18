import asyncio
import re
import html

from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
from aiogram.dispatcher.event.bases import SkipHandler

from app.services.access import is_pro
from app.services.ui_session import set_ui_message, get_ui_message
from app.ui.keyboards import pro_locked_keyboard
from app.services.ai_provider import AIProvider
from app.storage.pro_scenario_store import (
    get_scenario, upsert_stage1, upsert_stage2, upsert_stage3
)

router = Router()
ai: AIProvider | None = None

DRY_RUN_NO_GPT = False

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

STATE: dict[int, dict] = {}


# ---------- UI helpers ----------

def scenario_menu_keyboard() -> InlineKeyboardMarkup:
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


def _question_text(index: int) -> str:
    return f"{index + 1}) {QUESTIONS[index]}"


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


async def _send_scenario_menu(message: Message):
    await message.answer("🧩 Меню «Сценарный анализ жизни»", reply_markup=scenario_menu_keyboard())


# ---------- Telegram HTML helpers ----------

_ALLOWED_TAGS = ["b", "i", "code", "blockquote"]


def sanitize_telegram_html(text: str) -> str:
    if not text:
        return ""

    placeholders: dict[str, str] = {}
    out = text

    def _put(tag_text: str) -> str:
        key = f"__TAG_{len(placeholders)}__"
        placeholders[key] = tag_text
        return key

    for tag in _ALLOWED_TAGS:
        out = re.sub(fr"</{tag}>", lambda m: _put(m.group(0)), out)
        out = re.sub(fr"<{tag}>", lambda m: _put(m.group(0)), out)

    out = html.escape(out, quote=False)

    for key, tag_text in placeholders.items():
        out = out.replace(key, tag_text)

    return out


async def _send_long_html(message: Message, raw_html_text: str, limit: int = 3500):
    safe = sanitize_telegram_html(raw_html_text).strip()
    if not safe:
        await message.answer("Пустой ответ.")
        return

    paragraphs = safe.split("\n\n")
    chunk = ""

    for p in paragraphs:
        p = p.strip()
        if not p:
            continue

        candidate = (chunk + "\n\n" + p).strip() if chunk else p
        if len(candidate) <= limit:
            chunk = candidate
            continue

        if chunk:
            await message.answer(chunk, parse_mode="HTML")
            chunk = p
        else:
            t = p
            while t:
                await message.answer(t[:limit], parse_mode="HTML")
                t = t[limit:]
            chunk = ""

    if chunk:
        await message.answer(chunk, parse_mode="HTML")


# ---------- prompts ----------

ROLE_INTRO = (
    "Представь, что ты опытный транзактный аналитик, психолог с 30-летним стажем и умеешь прогнозировать будущее человека, "
    "учитывая его жизненный сценарий, условия экономики государства, в котором живет человек, и учитываешь возраст и силу "
    "сопротивления к изменениям, относительно сценария, по которому живет человек."
)


def _formatting_and_structure_rules_stage1() -> str:
    return (
        "ФОРМАТ ОТВЕТА (строго соблюдай):\n"
        "— Пиши в Telegram HTML: используй ТОЛЬКО теги <b>, <i>, <code>, <blockquote>.\n"
        "— Никаких других HTML тегов. Никакого Markdown.\n"
        "— Заголовки разделов делай жирными.\n"
        "— Подзаголовки/вставки делай курсивом.\n"
        "— Списки оформляй только маркерами «•».\n"
        "— Между разделами оставляй 1 пустую строку.\n\n"
        "Структура (строго):\n"
        "<b>🧠 1. Твоя базовая жизненная позиция и сценарный фундамент</b>\n\n"
        "<b>🎭 2. Твой сценарий по транзактному анализу</b>\n"
        "<i>Вероятный базовый сценарий</i>\n"
        "<i>Ключевые признаки</i> (список •)\n"
        "<i>Эго-состояния</i>: Родитель / Взрослый / Ребёнок\n"
        "<i>Внутренний конфликт</i>\n\n"
        "<b>🎯 3. Интересы и их скрытый потенциал</b>\n\n"
        "<b>🌍 4. Экономико-политический контекст (реалистично)</b>\n\n"
        "<b>🧱 5. Твоё сопротивление изменениям</b>\n\n"
        "<b>🔮 6. Прогноз по жизненным траекториям</b>\n"
        "<i>📉 Если сценарий не менять</i>\n"
        "<i>📈 Если сценарий скорректировать</i>\n\n"
        "<b>⚡ 7. Ключевая точка роста (самое важное)</b>\n\n"
        "<b>🧾 8. Итоговое описание тебя как личности</b>\n"
        "Характеристики (только список •, 6–10 пунктов)\n"
    )


def _build_stage1_prompt(answers: dict[int, str]) -> str:
    parts = [
        f"{ROLE_INTRO}\n\n"
        "Проанализируй следующие данные обо мне:\n"
    ]

    for i, q in enumerate(QUESTIONS):
        a = (answers.get(i) or "").strip()
        parts.append(f"{q} {a}\n")

    parts.append(
        "\nНа основе этих данных создай детальное описание.\n\n"
        "Требования к результату:\n"
        "– 450–500 слов (строго)\n"
        "– Без рекомендаций\n"
        "– Без советов\n"
        "– Без клинических диагнозов\n"
        "– Без ссылок на теории и модели\n\n"
        "Также после описания напиши его короткую выжимку на 200–250 слов (строго).\n\n"
        f"{_formatting_and_structure_rules_stage1()}\n\n"
        "Ответ выдай строго в формате:\n"
        "===FULL===\n"
        "<полное описание>\n"
        "===SUMMARY===\n"
        "<короткая выжимка>\n"
    )
    return "".join(parts)


def _build_stage2_system(summary: str) -> str:
    return (
        f"{ROLE_INTRO}\n\n"
        "Ниже — контекст (короткая выжимка предыдущего этапа):\n"
        f"{summary}\n\n"
        "Правила:\n"
        "— Пиши в Telegram HTML: только теги <b>, <i>, <code>, <blockquote>.\n"
        "— Текст строго 200–250 слов (не больше).\n"
        "— Структурно, без полотна.\n"
        "— Без рекомендаций/советов/диагнозов.\n"
    )


def _build_stage2_user() -> str:
    return (
        "На основании информации обо мне покажи три события, которые меня ждут, если я не выйду из жизненного сценария, "
        "согласно транзактного анализа, какие у них будут последствия, и как они отразятся на мне и моем здоровье.\n\n"
        "Структура (строго):\n"
        "<b>🔻 Этап 2: 3 события, если сценарий не менять</b>\n"
        "<b>1) Событие</b>: (краткое название)\n"
        "• Почему случится\n"
        "• Последствия\n"
        "• Отражение на здоровье/самочувствии\n\n"
        "<b>2) Событие</b>: (краткое название)\n"
        "• Почему случится\n"
        "• Последствия\n"
        "• Отражение на здоровье/самочувствии\n\n"
        "<b>3) Событие</b>: (краткое название)\n"
        "• Почему случится\n"
        "• Последствия\n"
        "• Отражение на здоровье/самочувствии\n\n"
        "Ответ выдай строго в формате:\n"
        "===STAGE2===\n"
        "<текст>\n"
    )


def _build_stage3_system(summary: str) -> str:
    return (
        f"{ROLE_INTRO}\n\n"
        "Ниже — контекст (короткая выжимка предыдущего этапа):\n"
        f"{summary}\n\n"
        "Правила:\n"
        "— Пиши в Telegram HTML: только теги <b>, <i>, <code>, <blockquote>.\n"
        "— Текст строго 200–250 слов (не больше).\n"
        "— Структурно, без полотна.\n"
        "— Без рекомендаций/советов/диагнозов.\n"
    )


def _build_stage3_user() -> str:
    return (
        "Опиши один день из моей жизни через 5 лет, включая детали, о которых я сейчас даже не задумываюсь:\n"
        "Мои привычки —\n"
        "Образ мышления —\n"
        "С кем я живу —\n"
        "Как выгляжу —\n"
        "Как я себя чувствую —\n\n"
        "Структура (строго):\n"
        "<b>🔮 Этап 3: Один день через 5 лет</b>\n"
        "<b>🌅 Утро</b>\n"
        "• Привычки\n"
        "• Состояние/ощущения\n\n"
        "<b>🏙 День</b>\n"
        "• Образ мышления\n"
        "• Люди рядом / с кем живу\n\n"
        "<b>🌙 Вечер</b>\n"
        "• Как выгляжу\n"
        "• Как я себя чувствую\n\n"
        "Ответ выдай строго в формате:\n"
        "===STAGE3===\n"
        "<текст>\n"
    )


def _parse_between(text: str, a: str, b: str) -> str:
    if not text or a not in text:
        return ""
    after = text.split(a, 1)[1]
    if b and b in after:
        return after.split(b, 1)[0].strip()
    return after.strip()


# ---------- flow ----------

def _init_user(tg_id: int):
    STATE[tg_id] = {"q": 0, "answers": {}}


@router.callback_query(F.data == "pro:scenario")
async def scenario_entry(cb: CallbackQuery):
    await _safe_answer(cb)
    tg_id = cb.from_user.id
    set_ui_message(tg_id, cb.message.chat.id, cb.message.message_id)

    if not is_pro(tg_id):
        await _render_ui(cb.message, tg_id, "Эта функция доступна только в ⭐ PRO.", reply_markup=pro_locked_keyboard())
        return

    await _render_ui(
        cb.message,
        tg_id,
        "🧩 Сценарный анализ жизни\n\n"
        "Тест состоит из 3 этапов:\n"
        "1) Сценарный анализ по вашим данным\n"
        "2) 3 события, если не выйти из сценария\n"
        "3) Один день через 5 лет\n\n"
        "Выберите действие:",
        reply_markup=scenario_menu_keyboard()
    )


@router.callback_query(F.data == f"{PREFIX}:start")
async def start_test(cb: CallbackQuery):
    """
    ✅ Новая логика:
    - если stage1 уже есть в БД → отправляем сохранённый результат (FULL + SUMMARY)
    - если нет → запускаем опрос
    """
    await _safe_answer(cb)
    tg_id = cb.from_user.id
    set_ui_message(tg_id, cb.message.chat.id, cb.message.message_id)

    if not is_pro(tg_id):
        await _render_ui(cb.message, tg_id, "Эта функция доступна только в ⭐ PRO.", reply_markup=pro_locked_keyboard())
        return

    saved = await get_scenario(tg_id)
    stage1_full = saved.get("stage1", {}).get("analysis_full") if saved else None
    stage1_summary = saved.get("stage1", {}).get("analysis_short") if saved else None

    if stage1_full:
        await cb.message.answer("✅ Этап 1 уже пройден. Отправляю сохранённый результат:")
        await _send_long_html(cb.message, stage1_full)
        if stage1_summary:
            await cb.message.answer("📌 Короткая выжимка:")
            await _send_long_html(cb.message, stage1_summary)
        await _send_scenario_menu(cb.message)
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
        await _render_ui(cb.message, tg_id, "🧩 Сценарный анализ жизни\n\nВыберите действие:", reply_markup=scenario_menu_keyboard())
        return

    st = STATE[tg_id]

    if st["q"] <= 0:
        await _render_ui(cb.message, tg_id, "🧩 Сценарный анализ жизни\n\nВыберите действие:", reply_markup=scenario_menu_keyboard())
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
        await _force_new_ui(message, tg_id, "Готово ✅\n\nЗапускаю Этап 1 (GPT)…")
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
    try:
        st = STATE.get(tg_id)
        if not st:
            return

        answers = st["answers"]
        qa = [{"q": QUESTIONS[i], "a": answers.get(i, "")} for i in range(len(QUESTIONS))]
        prompt = _build_stage1_prompt(answers)

        await upsert_stage1(tg_id=tg_id, qa=qa, analysis_full=None, analysis_short=None)

        if DRY_RUN_NO_GPT:
            await message.answer("Тестовый режим: GPT не вызываем.")
            await _send_long_html(message, f"<b>FINAL PROMPT:</b>\n\n{prompt}")
            await _send_scenario_menu(message)
            return

        if not ai:
            await message.answer("❌ AI не настроен (ai=None).")
            await _send_scenario_menu(message)
            return

        resp = await ai.generate(
            system_prompt=prompt,
            user_text="Сгенерируй ответ строго по формату. Не добавляй ничего кроме FULL и SUMMARY."
        )

        full = _parse_between(resp, "===FULL===", "===SUMMARY===")
        summary = _parse_between(resp, "===SUMMARY===", "")

        if not full:
            await message.answer("❌ Не удалось распарсить FULL. Ниже сырой ответ:")
            await _send_long_html(message, resp)
            await _send_scenario_menu(message)
            return

        await upsert_stage1(tg_id=tg_id, qa=qa, analysis_full=full, analysis_short=summary)

        await message.answer("✅ Этап 1 готов. Отправляю результат:")
        await _send_long_html(message, full)

        if summary:
            await message.answer("📌 Короткая выжимка:")
            await _send_long_html(message, summary)

        await _send_scenario_menu(message)

    except Exception as e:
        await message.answer(f"❌ Ошибка при генерации Stage 1: {e}")
        await _send_scenario_menu(message)

    finally:
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
    summary = saved.get("stage1", {}).get("analysis_short") if saved else None

    if not summary:
        await cb.message.answer("Сначала нужно пройти этап 1 (сценарный анализ).")
        await _send_scenario_menu(cb.message)
        return

    existing = saved.get("stage2", {}).get("text") if saved else None
    if existing:
        await cb.message.answer("✅ Этап 2 уже рассчитан. Отправляю снова:")
        await _send_long_html(cb.message, existing)
        await _send_scenario_menu(cb.message)
        return

    if DRY_RUN_NO_GPT:
        await cb.message.answer("Тестовый режим: GPT не вызываем.")
        await _send_scenario_menu(cb.message)
        return

    if not ai:
        await cb.message.answer("❌ AI не настроен (ai=None).")
        await _send_scenario_menu(cb.message)
        return

    system_prompt = _build_stage2_system(summary)
    user_text = _build_stage2_user()

    resp = await ai.generate(system_prompt=system_prompt, user_text=user_text)
    text = _parse_between(resp, "===STAGE2===", "")

    if not text:
        await cb.message.answer("❌ Не удалось распарсить STAGE2. Ниже сырой ответ:")
        await _send_long_html(cb.message, resp)
        await _send_scenario_menu(cb.message)
        return

    await upsert_stage2(tg_id, text)
    await cb.message.answer("✅ Этап 2 готов:")
    await _send_long_html(cb.message, text)
    await _send_scenario_menu(cb.message)


@router.callback_query(F.data == f"{PREFIX}:stage3")
async def stage3(cb: CallbackQuery):
    await _safe_answer(cb)
    tg_id = cb.from_user.id
    set_ui_message(tg_id, cb.message.chat.id, cb.message.message_id)

    if not is_pro(tg_id):
        await _render_ui(cb.message, tg_id, "Эта функция доступна только в ⭐ PRO.", reply_markup=pro_locked_keyboard())
        return

    saved = await get_scenario(tg_id)
    summary = saved.get("stage1", {}).get("analysis_short") if saved else None

    if not summary:
        await cb.message.answer("Сначала нужно пройти этап 1 (сценарный анализ).")
        await _send_scenario_menu(cb.message)
        return

    existing = saved.get("stage3", {}).get("text") if saved else None
    if existing:
        await cb.message.answer("✅ Этап 3 уже рассчитан. Отправляю снова:")
        await _send_long_html(cb.message, existing)
        await _send_scenario_menu(cb.message)
        return

    if DRY_RUN_NO_GPT:
        await cb.message.answer("Тестовый режим: GPT не вызываем.")
        await _send_scenario_menu(cb.message)
        return

    if not ai:
        await cb.message.answer("❌ AI не настроен (ai=None).")
        await _send_scenario_menu(cb.message)
        return

    system_prompt = _build_stage3_system(summary)
    user_text = _build_stage3_user()

    resp = await ai.generate(system_prompt=system_prompt, user_text=user_text)
    text = _parse_between(resp, "===STAGE3===", "")

    if not text:
        await cb.message.answer("❌ Не удалось распарсить STAGE3. Ниже сырой ответ:")
        await _send_long_html(cb.message, resp)
        await _send_scenario_menu(cb.message)
        return

    await upsert_stage3(tg_id, text)
    await cb.message.answer("✅ Этап 3 готов:")
    await _send_long_html(cb.message, text)
    await _send_scenario_menu(cb.message)
