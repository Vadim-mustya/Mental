import asyncio
import yaml
from pathlib import Path

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.exceptions import TelegramBadRequest


from app.ui.keyboards import main_menu_keyboard, question_keyboard, custom_keyboard
from app.services.ai_provider import AIProvider
from app.storage.users_store import save_fitness_profile_result  # пока используем текущую функцию хранилища
from app.services.ui_session import set_ui_message, get_ui_message
from aiogram.dispatcher.event.bases import SkipHandler


router = Router()

# пока AI подключён, но в финале теста НЕ вызываем (тестовый режим)
ai: AIProvider | None = None

PREFIX = "mental"  # callback prefix: mental:...

# лимиты для "свой вариант"
MAX_CUSTOM_CHARS = 1000
ASK_TO_SHORTEN_TO = 800

# флаг тестового режима: не тратим деньги на GPT
DRY_RUN_NO_GPT = True

# -------- YAML загрузка надёжно (через Path) --------
BASE_DIR = Path(__file__).resolve().parents[2]  # корень проекта
TEST_PATH = BASE_DIR / "config" / "mental_test.yaml"

with open(TEST_PATH, "r", encoding="utf-8") as f:
    TEST = yaml.safe_load(f)

QUESTIONS = TEST["questions"]
TITLE = TEST.get("title", "Психологический портрет")

# “чистые” вопросы для финального промта (без подсказок)
PROMPT_QUESTIONS = [
    "Какие эмоции вы переживаете чаще всего в повседневной жизни:",
    "Какие эмоции вам сложнее всего признавать:",
    "Как вы реагируете на критику:",
    "Что происходит внутри, когда что-то идет не по вашему плану:",
    "От чего в основном зависит ваше чувство собственной ценности:",
    "Что может заставить вас чувствовать себя «недостаточным»:",
    "Как вы ведете себя в конфликте:",
    "Когда у вас что-то не получается:",
    "Как звучит ваш внутренний голос в моменты ошибки:",
    "Есть ли в вашей жизни повторяющийся сюжет:",
]

STATE: dict[int, dict] = {}


def _init_user(tg_id: int):
    STATE[tg_id] = {
        "q": 0,
        "answers": {},          # int -> str
        "awaiting_custom": False,
        "awaiting_q": None,     # int
    }


def _q_text(i: int) -> str:
    return f"Вопрос {i + 1}/{len(QUESTIONS)}:\n{QUESTIONS[i]['text']}"


def _is_finished(tg_id: int) -> bool:
    return STATE[tg_id]["q"] >= len(QUESTIONS)


def _strip_option_prefix(text: str) -> str:
    """
    Убирает префиксы вида 'A) ' / 'B) ' и т.п. в начале строки.
    Пример: 'A) Тревога о будущем' -> 'Тревога о будущем'
    """
    t = (text or "").strip()
    if len(t) >= 3 and t[0].isalpha() and t[1] == ")" and t[2] == " ":
        return t[3:].strip()
    if len(t) >= 2 and t[0].isalpha() and t[1] == ")":
        return t[2:].strip()
    return t


async def _safe_answer_callback(cb: CallbackQuery):
    try:
        await cb.answer()
    except TelegramBadRequest:
        pass


async def _render_ui(message: Message, tg_id: int, text: str, reply_markup=None, parse_mode: str | None = None):
    """
    UI всегда должен быть последним сообщением:
    - если текущая UI-панель всё ещё последняя -> редактируем её
    - если после неё появились сообщения -> создаём новую UI-панель
    """
    ui = get_ui_message(tg_id)

    if not ui:
        sent = await message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
        set_ui_message(tg_id, sent.chat.id, sent.message_id)
        return

    chat_id, ui_msg_id = ui

    # если текущее событие новее чем UI — UI не последний, создаём новый
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
    """Всегда создаёт новую UI-панель и делает её последним сообщением."""
    sent = await message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
    set_ui_message(tg_id, sent.chat.id, sent.message_id)


def _build_prompt_prefix() -> str:
    return (
        "Ты — клинический психолог с 20-летним опытом, работающий на стыке психодинамики, "
        "поведенческого анализа и теории привязанности.\n\n"
        "Проведи глубокий анализ личности на основе следующих ответов:\n"
    )


def _build_prompt_suffix() -> str:
    return (
        "\n\n"
        "Задача — не пересказывать ответы и не делать сухой отчёт.\n"
        "Создай цельный психологический портрет личности.\n\n"
        "Требования к результату:\n"
        "– 1400–1500 слов\n"
        "– Без рекомендаций\n"
        "– Без советов\n"
        "– Без «что делать»\n"
        "– Без клинических диагнозов\n"
        "– Без ссылок на теории и модели\n"
        "– Без типологий (MBTI и т.д.)\n\n"
        "Текст должен звучать как точное попадание в структуру личности.\n"
        "Раскрой:\n"
        "– базовый характер\n"
        "– внутренние конфликты\n"
        "– скрытые страхи\n"
        "– механизм выгорания\n"
        "– способ переживания стыда и амбиций\n"
        "– стратегию самозащиты\n"
        "– повторяющиеся внутренние циклы\n"
        "– отношение к признанию и сравнению\n"
        "– глубинную уязвимость\n\n"
        "Стиль:\n"
        "– профессиональный, но живой\n"
        "– чуть эмоциональный\n"
        "– с лёгкой жёсткостью\n"
        "– с эффектом «это про меня»\n"
        "– без сглаживания острых углов\n\n"
        "Добавь один абзац, который сформулирует главную внутреннюю боль этого человека так, "
        "как он сам не смог бы её сформулировать.\n"
        "В конце не добавляй выводов, рекомендаций или предложений. Только портрет личности."
    )


def _build_answers_block(answers: dict[int, str]) -> str:
    """
    Блок ответов для промта: только "чистые" вопросы без подсказок,
    и ответ пользователя в кавычках “...”.
    """
    lines = []
    total = min(len(PROMPT_QUESTIONS), len(QUESTIONS))

    for i in range(total):
        q = PROMPT_QUESTIONS[i].strip()
        a = (answers.get(i) or "").strip()
        lines.append(f"{q}\n“{a}”\n")

    return "\n".join(lines).strip()





@router.callback_query(F.data == f"{PREFIX}:home")
async def home(cb: CallbackQuery):
    await _safe_answer_callback(cb)
    tg_id = cb.from_user.id

    # делаем текущее сообщение UI
    set_ui_message(tg_id, cb.message.chat.id, cb.message.message_id)

    STATE.pop(tg_id, None)
    await _render_ui(
        cb.message,
        tg_id,
        "Главное меню 👇",
        reply_markup=main_menu_keyboard(),
    )


@router.callback_query(F.data == f"{PREFIX}:start")
async def start_test(cb: CallbackQuery):
    await _safe_answer_callback(cb)
    tg_id = cb.from_user.id
    set_ui_message(tg_id, cb.message.chat.id, cb.message.message_id)

    _init_user(tg_id)
    await _render_ui(
        cb.message,
        tg_id,
        _q_text(0),
        reply_markup=question_keyboard(PREFIX, 0, QUESTIONS[0]["options"]),
    )


@router.callback_query(F.data == f"{PREFIX}:back")
async def back(cb: CallbackQuery):
    await _safe_answer_callback(cb)
    tg_id = cb.from_user.id
    set_ui_message(tg_id, cb.message.chat.id, cb.message.message_id)

    if tg_id not in STATE:
        await _render_ui(
            cb.message,
            tg_id,
            "Тест сбросился. Нажми «Психологический портрет (Free)» ещё раз 🙂",
            reply_markup=main_menu_keyboard(),
        )
        return

    st = STATE[tg_id]
    st["awaiting_custom"] = False
    st["awaiting_q"] = None
    st["q"] = max(0, st["q"] - 1)

    q = st["q"]
    await _render_ui(
        cb.message,
        tg_id,
        _q_text(q),
        reply_markup=question_keyboard(PREFIX, q, QUESTIONS[q]["options"]),
    )


@router.callback_query(F.data.startswith(f"{PREFIX}:ans:"))
async def answer(cb: CallbackQuery):
    await _safe_answer_callback(cb)
    tg_id = cb.from_user.id
    set_ui_message(tg_id, cb.message.chat.id, cb.message.message_id)

    if tg_id not in STATE:
        await _render_ui(
            cb.message,
            tg_id,
            "Тест сбросился. Нажми «Психологический портрет (Free)» ещё раз 🙂",
            reply_markup=main_menu_keyboard(),
        )
        return

    # mental:ans:{q_index}:{opt_id}
    _, _, q_index_str, opt_id = cb.data.split(":", 3)
    q_index = int(q_index_str)

    q = QUESTIONS[q_index]
    opt = next((o for o in q["options"] if o["id"] == opt_id), None)
    if not opt:
        return

    st = STATE[tg_id]

    # свой вариант
    if opt_id == "custom":
        st["awaiting_custom"] = True
        st["awaiting_q"] = q_index
        await _render_ui(
            cb.message,
            tg_id,
            "✍ Можно ответить коротко, либо расписать более подробно в рамках одного предложения.\n\n"
            f"Ограничение: до {MAX_CUSTOM_CHARS} символов.",
            reply_markup=custom_keyboard(PREFIX),
        )
        return

    # обычный ответ: сохраняем без A)/B)...
    st["answers"][q_index] = _strip_option_prefix(opt["text"])
    st["q"] = q_index + 1

    if _is_finished(tg_id):
        await _render_ui(cb.message, tg_id, "Готово ✅\n\nСобираю запрос…", reply_markup=None)
        asyncio.create_task(_finish(cb.message, tg_id))
        return

    nq = st["q"]
    await _render_ui(
        cb.message,
        tg_id,
        _q_text(nq),
        reply_markup=question_keyboard(PREFIX, nq, QUESTIONS[nq]["options"]),
    )


@router.message(F.text)
async def custom_text(message: Message):
    tg_id = message.from_user.id
    if tg_id not in STATE:
        raise SkipHandler

    st = STATE[tg_id]
    if not st.get("awaiting_custom"):
        raise SkipHandler

    text = (message.text or "").strip()
    if not text:
        await message.answer("Ответ пустой. Напиши хотя бы пару слов 🙂")
        return

    if len(text) > MAX_CUSTOM_CHARS:
        await message.answer(
            f"Ответ слишком длинный (>{MAX_CUSTOM_CHARS} символов).\n"
            f"Пожалуйста, сократи до {ASK_TO_SHORTEN_TO} символов."
        )
        return

    q_index = st["awaiting_q"]
    st["answers"][q_index] = text
    st["awaiting_custom"] = False
    st["awaiting_q"] = None
    st["q"] = q_index + 1

    # после сообщения пользователя UI должен стать последним -> _render_ui создаст новую панель
    if _is_finished(tg_id):
        await _render_ui(message, tg_id, "Готово ✅\n\nСобираю запрос…", reply_markup=None)
        asyncio.create_task(_finish(message, tg_id))
        return

    nq = st["q"]
    await _render_ui(
        message,
        tg_id,
        _q_text(nq),
        reply_markup=question_keyboard(PREFIX, nq, QUESTIONS[nq]["options"]),
    )


async def _finish(message: Message, tg_id: int):
    st = STATE.get(tg_id, {})
    answers: dict[int, str] = st.get("answers", {})

    answers_block = _build_answers_block(answers)
    final_prompt = _build_prompt_prefix() + "\n\n" + answers_block + _build_prompt_suffix()

    # Сохраняем ответы (пока используем старую функцию и json-хранилище)
    try:
        await save_fitness_profile_result(tg_id=tg_id, answers=answers)
    except Exception:
        pass

    if DRY_RUN_NO_GPT:
        await message.answer(
            "✅ Тест завершён.\n\n"
            "Запрос успешно сформирован и готов к отправке в GPT.\n"
            "(Сейчас тестовый режим — GPT не вызываем.)"
        )

        payload = f"FINAL PROMPT (то, что уйдёт в GPT):\n\n{final_prompt}"
        if len(payload) > 3800:
            payload = payload[:3800] + "\n\n…(обрезано для Telegram, в реальном запросе будет полностью)"
        await message.answer(payload)
    else:
        await message.answer("Реальный вызов GPT ещё не включён.")

    await _force_new_ui(
        message,
        tg_id,
        "Главное меню 👇",
        reply_markup=main_menu_keyboard(),
    )

    STATE.pop(tg_id, None)
