"""Small, dependency-free translation catalog for future English/Russian UI.

Migration strategy: UI labels use stable keys; user preference is eventually
stored with QSettings (global, not inside the animation project). The current
editor remains predominantly English; this catalog is groundwork, not a
complete Russian localization.
"""
from __future__ import annotations

SUPPORTED_LOCALES = ("en", "ru")

MESSAGES = {
    "frames.new_at_end": {"en": "New frame at end", "ru": "Новый кадр в конце"},
    "frames.duplicate_current": {"en": "Duplicate current frame", "ru": "Дублировать текущий кадр"},
    "frames.show": {"en": "Show in playback", "ru": "Показывать при воспроизведении"},
    "frames.hide": {"en": "Hide from playback", "ru": "Скрыть при воспроизведении"},
    "frames.duplicate": {"en": "Duplicate frame", "ru": "Дублировать кадр"},
    "frames.insert_after": {"en": "Insert frame after", "ru": "Вставить кадр после"},
    "frames.move_up": {"en": "Move frame up", "ru": "Переместить кадр выше"},
    "frames.move_down": {"en": "Move frame down", "ru": "Переместить кадр ниже"},
    "frames.delete": {"en": "Delete frame", "ru": "Удалить кадр"},
    "frames.title": {"en": "Frames", "ru": "Кадры"},
    "elements.title": {"en": "Elements", "ru": "Элементы"},
    "playback.title": {"en": "Playback", "ru": "Воспроизведение"},
}


def tr(key: str, locale: str = "en") -> str:
    item = MESSAGES.get(key)
    if item is None:
        return key
    return item.get(locale, item["en"])
