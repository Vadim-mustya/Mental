import json
import asyncio
from pathlib import Path
from datetime import datetime, timezone

_LOCK = asyncio.Lock()

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_PATH = BASE_DIR / "data" / "pro_scenario.json"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _load() -> dict:
    if not DATA_PATH.exists():
        return {"users": {}}
    try:
        text = DATA_PATH.read_text(encoding="utf-8")
        return json.loads(text) if text.strip() else {"users": {}}
    except Exception:
        return {"users": {}}


async def _save(db: dict) -> None:
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")


async def get_scenario(tg_id: int) -> dict | None:
    async with _LOCK:
        db = await _load()
        return db.get("users", {}).get(str(tg_id))


async def upsert_stage1(
    tg_id: int,
    qa: list[dict],
    analysis_full: str | None = None,
    analysis_short: str | None = None,
) -> None:
    async with _LOCK:
        db = await _load()
        users = db.setdefault("users", {})
        u = users.setdefault(str(tg_id), {})
        u["updated_at"] = _utc_now_iso()
        u["stage1"] = {
            "qa": qa,
            "analysis_full": analysis_full,
            "analysis_short": analysis_short,
        }
        await _save(db)


async def upsert_stage2(tg_id: int, text: str) -> None:
    async with _LOCK:
        db = await _load()
        users = db.setdefault("users", {})
        u = users.setdefault(str(tg_id), {})
        u["updated_at"] = _utc_now_iso()
        u["stage2"] = {"text": text}
        await _save(db)


async def upsert_stage3(tg_id: int, text: str) -> None:
    async with _LOCK:
        db = await _load()
        users = db.setdefault("users", {})
        u = users.setdefault(str(tg_id), {})
        u["updated_at"] = _utc_now_iso()
        u["stage3"] = {"text": text}
        await _save(db)
