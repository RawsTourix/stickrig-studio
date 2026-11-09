"""Future localization boundary for StickRig UI strings.

The editor is currently English-first. Strings introduced by future
iterations should use tr(key, locale) rather than embedding translations.
Switching the *whole* application language is not available yet.
"""
from __future__ import annotations

from functools import lru_cache

SUPPORTED_LOCALES = ("en", "ru")

_CATALOG = {
    "en": {
        "frames.title": "Frames",
        "frames.add": "Add frame",
        "frames.add_end": "Add frame at end",
        "frames.duplicate": "Duplicate frame",
        "frames.delete": "Delete frame",
        "frames.hide": "Hide frame",
        "frames.show": "Show frame",
        "frames.move_up": "Move frame up",
        "frames.move_down": "Move frame down",
        "frames.unhide_all": "Show all hidden frames",
        "frames.reorder": "Drag to reorder",
        "preview.play": "Play",
        "preview.pause": "Pause",
        "preview.fps": "FPS",
        "canvas.zoom": "Viewport zoom",
        "canvas.pan": "Move canvas",
        "elements.title": "Elements",
    },
    "ru": {
        "frames.title": "Кадры",
        "frames.add": "Добавить кадр",
        "frames.add_end": "Добавить кадр в конец",
        "frames.duplicate": "Дублировать кадр",
        "frames.delete": "Удалить кадр",
        "frames.hide": "Скрыть кадр",
        "frames.show": "Показать кадр",
        "frames.move_up": "Переместить кадр вверх",
        "frames.move_down": "Переместить кадр вниз",
        "frames.unhide_all": "Показать все скрытые кадры",
        "frames.reorder": "Перетащите для перестановки",
        "preview.play": "Воспроизвести",
        "preview.pause": "Пауза",
        "preview.fps": "Кадров/с",
        "canvas.zoom": "Масштаб рабочего поля",
        "canvas.pan": "Перемещение по сцене",
        "elements.title": "Элементы",
    },
}


@lru_cache(maxsize=256)
def tr(key: str, locale: str = "en") -> str:
    """Translate a stable message key. Fallback is English, then the key."""
    messages = _CATALOG.get(locale, _CATALOG["en"])
    return messages.get(key, _CATALOG["en"].get(key, key))


def check_catalogs() -> None:
    """Require matching message identifiers in both supported languages."""
    expected = set(_CATALOG["en"])
    for locale in SUPPORTED_LOCALES:
        if set(_CATALOG[locale]) != expected:
            raise ValueError(f"incomplete localization catalog: {locale}")
