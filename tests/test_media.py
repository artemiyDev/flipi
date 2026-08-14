from bot.services.media import extract_media_references, strip_media_references


def test_extract_media_references_preserves_order_and_deduplicates() -> None:
    refs = extract_media_references("[media:a.png] [sound:b.mp3]", "[media:a.png] [media:c.jpg]")

    assert refs == ["a.png", "b.mp3", "c.jpg"]


def test_strip_media_references() -> None:
    assert strip_media_references("Word [media:image.png] [sound:voice.mp3]") == "Word"
