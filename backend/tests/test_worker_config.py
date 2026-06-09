from app.worker import WorkerSettings, enqueue_device_polls, poll_device


def test_worker_settings_register_functions_and_cron():
    fn_names = {getattr(f, "__name__", getattr(f, "name", "")) for f in WorkerSettings.functions}
    assert "poll_device" in fn_names
    assert WorkerSettings.cron_jobs  # almeno un cron job (enqueue)
    assert callable(poll_device) and callable(enqueue_device_polls)


def test_worker_exposes_event_ingest():
    from app.worker import WorkerSettings, ingest_device_events

    assert ingest_device_events in WorkerSettings.functions
    # due cron: poll metriche + ingest eventi
    assert len(WorkerSettings.cron_jobs) >= 2
