from __future__ import annotations

import importlib.util
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

    assert config.database.proxy_enabled is False
    assert config.database.app_pool_size == 3
    assert config.database.app_max_overflow == 2
    assert config.database.app_pool_timeout_seconds == 10
    assert config.database.app_pool_recycle_seconds == 1800

    assert config.api.web_concurrency == 2
    assert config.api.requests_per_target == 800

    assert config.worker.queue_scale_up_depth == 25
    assert config.worker.queue_scale_down_depth == 2

    assert config.retention.clinical_record_days == 3650
    assert config.retention.minor_record_age_years == 28
    assert config.retention.recording_days == 90
    assert config.retention.sms_metadata_days == 2190
    assert config.retention.notification_days == 180
    assert config.retention.dead_letter_raw_days == 30
    assert config.retention.idempotency_days == 7
