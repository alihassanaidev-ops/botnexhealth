from __future__ import annotations

from pathlib import Path

from aws_cdk import App, Environment

from nex_health_infra.config import load_config
from nex_health_infra.stack import NexHealthPlatformStack


def _resolve_config_path(raw_path: str | None) -> Path:
    infra_root = Path(__file__).resolve().parent
    if not raw_path:
        return infra_root / "config" / "staging.json"

    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate
    return infra_root / candidate


app = App()
config_path = _resolve_config_path(app.node.try_get_context("config"))
config = load_config(config_path)

NexHealthPlatformStack(
    app,
    f"{config.app_name}-{config.environment_name}",
    config=config,
    env=Environment(account=config.account, region=config.region),
)

app.synth()
