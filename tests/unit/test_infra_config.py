from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INFRA_ROOT = ROOT / "infra"

spec = importlib.util.spec_from_file_location(
    "nex_health_infra_config",
    INFRA_ROOT / "nex_health_infra" / "config.py",
)
assert spec is not None
assert spec.loader is not None
nex_health_infra_config = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = nex_health_infra_config
spec.loader.exec_module(nex_health_infra_config)
load_config = nex_health_infra_config.load_config


def test_staging_config_loads_scale_controls() -> None:
    config = load_config(INFRA_ROOT / "config" / "staging.json")

    assert config.refresh_token_ttl_minutes == 8 * 60
    assert config.database.proxy_enabled is False
    assert config.database.app_pool_size == 3
    assert config.database.app_max_overflow == 2
    assert config.database.app_pool_timeout_seconds == 10
    assert config.database.app_pool_recycle_seconds == 1800

    assert config.api.web_concurrency == 2
    assert config.api.requests_per_target == 800

    assert config.worker.queue_scale_up_depth == 25
    assert config.worker.queue_scale_down_depth == 2
    assert config.waf_count_oversize_body_requests is True
    assert config.email.ses_sending_domain == "mail.staging.scalenexus.ai"
    assert config.email.ses_sending_hosted_zone_name == "staging.scalenexus.ai"
    assert config.email.ses_clinic_sending_enabled is False

    assert config.retention.clinical_record_days == 3650
    assert config.retention.minor_record_age_years == 28
    assert config.retention.recording_days == 90
    assert config.retention.sms_metadata_days == 2190
    assert config.retention.notification_days == 180
    assert config.retention.dead_letter_raw_days == 30
    assert config.retention.idempotency_days == 7


def test_production_keeps_oversize_body_waf_blocking() -> None:
    config = load_config(INFRA_ROOT / "config" / "production.json")

    assert config.refresh_token_ttl_minutes == 8 * 60
    assert config.waf_count_oversize_body_requests is False


def test_spoken_opt_out_is_configured_on_staging_only_for_now() -> None:
    staging = load_config(INFRA_ROOT / "config" / "staging.json")
    production = load_config(INFRA_ROOT / "config" / "production.json")

    # Matches the recall agent's post-call field name exactly.
    assert staging.retell_optout_analysis_key == "Do not call"
    # Production turns it on during the recall rollout, once the agent's
    # post-call analysis carries the field.
    assert production.retell_optout_analysis_key is None


def test_ses_activation_requires_a_sending_domain(tmp_path: Path) -> None:
    source = json.loads((INFRA_ROOT / "config" / "staging.json").read_text())
    source["email"]["sesClinicSendingEnabled"] = True
    source["email"].pop("sesSendingDomain")
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(source))

    try:
        load_config(path)
    except ValueError as exc:
        assert "sesSendingDomain" in str(exc)
    else:
        raise AssertionError("enabled SES sending without a domain must fail")


def test_production_scheduler_runs_only_the_campaign_jobs() -> None:
    production = load_config(INFRA_ROOT / "config" / "production.json")

    # Off until the recall launch switches it on; the list is already pinned
    # so turning it on cannot start the NexHealth syncs or other sweeps.
    assert production.scheduler.enabled is False
    assert production.scheduler.beat_tasks == [
        "tick-workflow-schedules",
        "poll-workflow-timers",
        "recover-stale-workflow-timers",
        "poll-retell-voice-outcomes",
    ]
    assert "-B" not in production.worker.command


def test_staging_runs_every_job_from_its_own_scheduler() -> None:
    staging = load_config(INFRA_ROOT / "config" / "staging.json")

    assert staging.scheduler.enabled is True
    assert staging.scheduler.beat_tasks == []
    assert "-B" not in staging.worker.command


def test_beat_in_the_worker_and_a_scheduler_cannot_both_run(tmp_path: Path) -> None:
    source = json.loads((INFRA_ROOT / "config" / "staging.json").read_text())
    source["worker"]["command"].insert(-1, "-B")
    path = tmp_path / "double-beat.json"
    path.write_text(json.dumps(source))

    try:
        load_config(path)
    except ValueError as exc:
        assert "-B" in str(exc)
    else:
        raise AssertionError("beat in the worker plus a scheduler must fail")
