from __future__ import annotations

from typing import Optional, Tuple

# tg_id -> (chat_id, message_id)
_UI: dict[int, tuple[int, int]] = {}


def set_ui_message(tg_id: int, chat_id: int, message_id: int) -> None:
    _UI[tg_id] = (chat_id, message_id)


def get_ui_message(tg_id: int) -> Optional[Tuple[int, int]]:
    return _UI.get(tg_id)


def clear_ui_message(tg_id: int) -> None:
    _UI.pop(tg_id, None)


def ui_is_last(known_ui_msg_id: int, current_msg_id: int) -> bool:
    """
    Примерная проверка "панель последняя?"
    Если после UI были сообщения, current_msg_id будет больше.
    """
    return known_ui_msg_id >= current_msg_id
