from stickrig.i18n import SUPPORTED_LOCALES, check_catalogs, tr


def test_catalog_keys_match_and_locales_exist():
    assert SUPPORTED_LOCALES == ("en", "ru")
    check_catalogs()
    assert tr("frames.title", "en") == "Frames"
    assert tr("frames.title", "ru") == "Кадры"


def test_translation_unknown_key_and_locale_fallback():
    assert tr("unknown.key", "ru") == "unknown.key"
    assert tr("preview.play", "es") == "Play"
