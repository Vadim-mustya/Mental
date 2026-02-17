from typing import Dict, Any
import yaml


def load_test_config() -> Dict[str, Any]:
    path = "config/test_fitness.yaml"
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(f"Тест-конфиг не прочитался или пустой: {path}")

    if "questions" not in data or not isinstance(data["questions"], list) or len(data["questions"]) == 0:
        raise ValueError(f"В тест-конфиге нет списка questions или он пустой: {path}")

    return data


TEST_CFG = load_test_config()
