import json
import os
from typing import Any


STRESS_PROFILE_PRIMARY = "test1/stress_profile.json"
STRESS_PROFILE_FALLBACK = "test1/stress_profile.example.json"


def load_stress_profile_file() -> dict[str, Any]:
    """
    Единая точка загрузки stress profile для всех Python-чекеров.
    Приоритет:
    1) test1/stress_profile.json
    2) test1/stress_profile.example.json
    """
    for path in (STRESS_PROFILE_PRIMARY, STRESS_PROFILE_FALLBACK):
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return {}

