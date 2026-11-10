from triage import kb


def test_search_finds_the_section_that_answers_the_question():
    top = kb.search("refund for yearly plan")[0]

    assert (top.doc_id, top.section) == ("refunds", "Yearly plans")


def test_search_returns_nothing_when_no_words_match():
    assert kb.search("xylophone zeppelin") == []
