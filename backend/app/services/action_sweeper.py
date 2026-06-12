"""Pure give-up policy for the orphaned-action sweeper (no DB/lock — unit-testable)."""


def decide_orphan(*, sweep_attempts: int, max_attempts: int) -> str:
    """Given how many device-free re-enqueues a scheduled orphan has already had, decide the action.

    Returns 're-enqueue' while attempts remain, else 'give-up'. The caller only invokes this once it
    has confirmed (via the device advisory lock) that the device is free — so attempts count only
    genuine device-free retries.
    """
    return "re-enqueue" if sweep_attempts < max_attempts else "give-up"
