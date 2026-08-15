from bot.services.media import extract_media_references, strip_media_references
from bot.services.study import sanitize_card_html


def test_extract_media_references_preserves_order_and_deduplicates() -> None:
    refs = extract_media_references("[media:a.png] [sound:b.mp3]", "[media:a.png] [media:c.jpg]")

    assert refs == ["a.png", "b.mp3", "c.jpg"]


def test_strip_media_references() -> None:
    assert strip_media_references("Word [media:image.png] [sound:voice.mp3]") == "Word"


def test_sanitize_card_html_keeps_formatting_and_local_media_only() -> None:
    rendered = sanitize_card_html(
        '<b>word</b><ul><li>item</li></ul><script>alert(1)</script>'
        '<img src="https://evil.example/x.png" onerror="alert(1)">'
        '<img src="/api/media/5" style="width: 1px">'
    )

    assert "<b>word</b>" in rendered
    assert "<ul><li>item</li></ul>" in rendered
    assert "script" not in rendered
    assert "evil.example" not in rendered
    assert "onerror" not in rendered
    assert rendered.count("<img") == 1
    assert '<img src="/api/media/5">' in rendered
