from app.services.action_sweeper import decide_orphan


def test_reenqueue_while_attempts_remain():
    assert decide_orphan(sweep_attempts=0, max_attempts=5) == "re-enqueue"
    assert decide_orphan(sweep_attempts=4, max_attempts=5) == "re-enqueue"


def test_give_up_at_or_past_max():
    assert decide_orphan(sweep_attempts=5, max_attempts=5) == "give-up"
    assert decide_orphan(sweep_attempts=9, max_attempts=5) == "give-up"
