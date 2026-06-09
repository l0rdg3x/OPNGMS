from app.worker import WorkerSettings, enqueue_device_polls, poll_device


def test_worker_settings_register_functions_and_cron():
    fn_names = {getattr(f, "__name__", getattr(f, "name", "")) for f in WorkerSettings.functions}
    assert "poll_device" in fn_names
    assert WorkerSettings.cron_jobs  # at least one cron job (enqueue)
    assert callable(poll_device) and callable(enqueue_device_polls)


def test_worker_exposes_event_ingest():
    from app.worker import WorkerSettings, ingest_device_events

    assert ingest_device_events in WorkerSettings.functions
    # two crons: metrics poll + events ingest
    assert len(WorkerSettings.cron_jobs) >= 2


def test_worker_exposes_config_backup():
    from app.worker import WorkerSettings, backup_device_config

    assert backup_device_config in WorkerSettings.functions
    # three crons: metrics poll + events ingest + config backup
    assert len(WorkerSettings.cron_jobs) >= 3


def test_worker_exposes_config_change_apply():
    from app.worker import WorkerSettings, apply_config_change

    assert apply_config_change in WorkerSettings.functions
