from app.list_progress import ListProgress


def test_stuck_is_not_confirmed_bottom():
    tracker = ListProgress()
    assert tracker.observe(["a"]) is None
    assert tracker.observe(["a"]) is None
    assert tracker.observe(["a"]) is None
    assert tracker.observe(["a"]) == "no_new_cards"


def test_new_cards_reset_stagnation():
    tracker = ListProgress()
    assert tracker.observe(["a"]) is None
    assert tracker.observe(["a"]) is None
    assert tracker.observe(["b"]) is None
    assert tracker.observe(["b"]) is None
    assert tracker.observe(["b"]) is None
    assert tracker.observe(["b"]) == "no_new_cards"


def test_loading_and_empty_list_do_not_prove_bottom():
    tracker = ListProgress()
    assert tracker.observe([], loading=True) is None
    assert tracker.observe([], loading=True) is None
    assert tracker.observe([], loading=True) == "loading_timeout"
    tracker = ListProgress()
    assert tracker.observe([]) is None
    assert tracker.observe([]) is None
    assert tracker.observe([]) == "unreadable_list"
