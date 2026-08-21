from bot.services.cards import parse_browser_query


def test_parse_browser_query_filters() -> None:
    query = parse_browser_query("bonjour tag:french deck:Languages state:new is:due has:flag")

    assert query.text_terms == ["bonjour"]
    assert query.tags == ["french"]
    assert query.decks == ["Languages"]
    assert query.states == ["new"]
    assert query.is_due is True
    assert query.has_flag is True


def test_parse_browser_query_state_and_flags_are_normalized() -> None:
    query = parse_browser_query("state:Review flag:Red is:suspended is:buried is:leech")

    assert query.states == ["review"]
    assert query.flags == ["red"]
    assert query.is_suspended is True
    assert query.is_buried is True
    assert query.is_leech is True
