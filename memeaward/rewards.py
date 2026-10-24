"""Reward limit validation shared by the desktop and Twitch adapter."""

LIMIT_MAXIMA = {
    "cooldown_seconds": 604800,
    "max_per_user_per_stream": 1_000_000,
    "max_per_stream": 1_000_000,
}


def normalize_limits(limits=None):
    if limits is None:
        limits = {}
    if not isinstance(limits, dict):
        raise ValueError("Ограничения награды должны быть настройками с числовыми значениями.")
    result = {}
    for key, maximum in LIMIT_MAXIMA.items():
        value = limits.get(key, 0)
        if type(value) is not int or not 0 <= value <= maximum:
            raise ValueError("Лимиты должны быть целыми неотрицательными числами; пауза — не больше 604800 секунд (7 дней).")
        result[key] = value
    return result


def twitch_limits(limits):
    limits = normalize_limits(limits)
    result = {}
    for setting, enabled_key, value_key in (
        ("cooldown_seconds", "is_global_cooldown_enabled", "global_cooldown_seconds"),
        ("max_per_user_per_stream", "is_max_per_user_per_stream_enabled", "max_per_user_per_stream"),
        ("max_per_stream", "is_max_per_stream_enabled", "max_per_stream"),
    ):
        value = limits[setting]
        result[enabled_key] = value > 0
        # Twitch's numeric fields have a minimum of 1. Disable via the flag, not value=0.
        if value:
            result[value_key] = value
    return result
