from stickrig.i18n import SUPPORTED_LOCALES, tr


def test_supported_locales_and_keys():
    assert set(SUPPORTED_LOCALES) == {"en", "ru"}
    assert tr("frames.delete", "ru") == "Удалить кадр"
    assert tr("frames.delete", "en") == "Delete frame"
    assert tr("frames.delete", "fr") == "Delete frame"
    assert tr("unknown", "ru") == "unknown"
