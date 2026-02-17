import json
import os
from pathlib import Path
from datetime import datetime, timezone
import asyncio

# users.json лежит в корне проекта: /data/users.json
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
USERS_FILE = DATA_DIR / "users.json"

# Чтобы два запроса одновременно не портили файл
_file_lock = asyncio.Lock()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_file():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not USERS_FILE.exists():
        USERS_FILE.write_text(json.dumps({"users": {}}, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_sync() -> dict:
    _ensure_file()
    try:
        raw = USERS_FILE.read_text(encoding="utf-8").strip()
        if not raw:
            return {"users": {}}
        data = json.loads(raw)
        if not isinstance(data, dict):
            return {"users": {}}
        if "users" not in data or not isinstance(data["users"], dict):
            data["users"] = {}
        return data
    except Exception:
        # Если файл битый — не падаем, а начинаем заново
        return {"users": {}}


def _write_sync(data: dict):
    _ensure_file()
    tmp = USERS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, USERS_FILE)


async def save_fitness_profile_result(
    tg_id: int,
    answers: dict,
):
    """
    Сохраняем только ФИНАЛ теста.
    answers: dict где ключи — индексы вопросов (0..7), значения — строки ответов.
    """
    async with _file_lock:
        data = _read_sync()

        users = data["users"]
        key = str(tg_id)

        user = users.get(key, {})
        if not isinstance(user, dict):
            user = {}

        user["tg_id"] = tg_id
        user["fitness_profile"] = {
            "completed_at": _utc_now_iso(),
            "answers": answers,
        }

        users[key] = user
        data["users"] = users

        _write_sync(data)


async def get_user(tg_id: int) -> dict | None:
    async with _file_lock:
        data = _read_sync()
        return data["users"].get(str(tg_id))


from datetime import datetime, timezone, timedelta

def _parse_iso(dt_str: str) -> datetime | None:
    try:
        # поддержка isoformat с timezone
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except Exception:
        return None


async def can_start_fitness_profile(tg_id: int, cooldown_days: int = 7) -> tuple[bool, str | None]:
    """
    Возвращает:
      (True, None) — можно запускать
      (False, msg) — нельзя, msg содержит текст когда можно
    """
    user = await get_user(tg_id)
    if not user:
        return True, None

    fp = user.get("fitness_profile")
    if not fp:
        return True, None

    completed_at = fp.get("completed_at")
    if not completed_at:
        return True, None

    dt = _parse_iso(completed_at)
    if not dt:
        return True, None

    now = datetime.now(timezone.utc)
    next_allowed = dt + timedelta(days=cooldown_days)

    if now >= next_allowed:
        return True, None

    # сколько осталось
    remaining = next_allowed - now
    hours = int(remaining.total_seconds() // 3600)
    days = hours // 24
    hours = hours % 24

    # когда можно (в UTC); можно потом сделать по МСК — позже
    when_str = next_allowed.strftime("%d.%m.%Y %H:%M UTC")

    msg = (
        "⏳ Фитнес-профиль в бесплатной версии можно проходить **1 раз в неделю**.\n\n"
        f"Следующая попытка будет доступна: **{when_str}**.\n"
        f"Осталось примерно: **{days} д {hours} ч**."
    )
    return False, msg

from datetime import datetime, timezone, timedelta

def _week_start_utc_iso(dt: datetime) -> str:
    # неделя с понедельника 00:00 UTC
    dt0 = dt.astimezone(timezone.utc)
    start = dt0 - timedelta(days=dt0.weekday(), hours=dt0.hour, minutes=dt0.minute, seconds=dt0.second, microseconds=dt0.microsecond)
    return start.isoformat()

def _parse_iso(dt_str: str) -> datetime | None:
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except Exception:
        return None


from datetime import datetime, timezone, timedelta

def _week_start_utc_iso(dt: datetime) -> str:
    dt0 = dt.astimezone(timezone.utc)
    start = dt0 - timedelta(
        days=dt0.weekday(),
        hours=dt0.hour,
        minutes=dt0.minute,
        seconds=dt0.second,
        microseconds=dt0.microsecond
    )
    return start.isoformat()

def _parse_iso(dt_str: str) -> datetime | None:
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except Exception:
        return None


async def can_use_free_nutrition(tg_id: int, limit_per_week: int = 3) -> tuple[bool, str | None]:
    """
    Только проверяем, НЕ списываем.
    """
    now = datetime.now(timezone.utc)
    week_start = _week_start_utc_iso(now)

    user = await get_user(tg_id)
    if not user:
        return True, None

    free = user.get("free_usage", {})
    if not isinstance(free, dict):
        return True, None

    nut = free.get("nutrition", {})
    if not isinstance(nut, dict):
        return True, None

    saved_week = nut.get("week_start")
    count = int(nut.get("count", 0) or 0)

    # новая неделя -> считаем, что доступно
    if saved_week != week_start:
        return True, None

    if count >= limit_per_week:
        ws_dt = _parse_iso(saved_week) or now
        next_week = ws_dt + timedelta(days=7)
        when_str = next_week.strftime("%d.%m.%Y %H:%M UTC")

        msg = (
            "⏳ Подбор рациона в бесплатной версии доступен **3 раза в неделю**.\n\n"
            f"Лимит на эту неделю исчерпан. Следующие попытки будут доступны: **{when_str}**."
        )
        return False, msg

    return True, None


async def consume_free_nutrition_use(tg_id: int, limit_per_week: int = 3) -> tuple[bool, str | None]:
    """
    Списываем 1 попытку. Вызывать ТОЛЬКО после успешного ответа GPT.
    """
    now = datetime.now(timezone.utc)
    week_start = _week_start_utc_iso(now)

    async with _file_lock:
        data = _read_sync()
        users = data["users"]
        key = str(tg_id)

        user = users.get(key, {})
        if not isinstance(user, dict):
            user = {}

        free = user.get("free_usage", {})
        if not isinstance(free, dict):
            free = {}

        nut = free.get("nutrition", {})
        if not isinstance(nut, dict):
            nut = {}

        saved_week = nut.get("week_start")
        count = int(nut.get("count", 0) or 0)

        # новая неделя -> сбрасываем
        if saved_week != week_start:
            saved_week = week_start
            count = 0

        if count >= limit_per_week:
            ws_dt = _parse_iso(saved_week) or now
            next_week = ws_dt + timedelta(days=7)
            when_str = next_week.strftime("%d.%m.%Y %H:%M UTC")

            msg = (
                "⏳ Подбор рациона в бесплатной версии доступен **3 раза в неделю**.\n\n"
                f"Лимит на эту неделю исчерпан. Следующие попытки будут доступны: **{when_str}**."
            )
            return False, msg

        count += 1
        nut["week_start"] = saved_week
        nut["count"] = count

        free["nutrition"] = nut
        user["free_usage"] = free
        user["tg_id"] = tg_id

        users[key] = user
        data["users"] = users
        _write_sync(data)

        return True, None

