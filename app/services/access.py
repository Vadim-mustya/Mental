import os


def _parse_int_list(value: str | None) -> set[int]:
    if not value:
        return set()
    parts = [p.strip() for p in value.replace(";", ",").split(",")]
    out = set()
    for p in parts:
        if p.isdigit():
            out.add(int(p))
    return out


def is_pro(tg_id: int) -> bool:
    """
    Пока оплаты нет, PRO можно включить через .env:
    PRO_TEST_IDS=12345,67890

    Позже сюда подключим реальную проверку подписки из БД/платежки.
    """
    test_ids = _parse_int_list(os.getenv("PRO_TEST_IDS"))
    return tg_id in test_ids
