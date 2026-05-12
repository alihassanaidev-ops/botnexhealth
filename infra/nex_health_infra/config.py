from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class NetworkConfig:
    vpc_id: str | None = None
    max_azs: int = 2
    nat_gateways: int = 1


@dataclass(frozen=True)
class DatabaseConfig:
    name: str
    username: str
    instance_type: str
    allocated_storage: int
    max_allocated_storage: int
    multi_az: bool
    backup_retention_days: int
    deletion_protection: bool
    engine_major_version: str
    engine_full_version: str
    proxy_enabled: bool = False
    app_pool_size: int = 5
    app_max_overflow: int = 5
    app_pool_timeout_seconds: int = 10
    app_pool_recycle_seconds: int = 1800


@dataclass(frozen=True)
class RedisConfig:
    node_type: str
    engine_version: str
    num_node_groups: int = 1
    replicas_per_node_group: int = 0
    automatic_failover_enabled: bool = False
    multi_az_enabled: bool = False


@dataclass(frozen=True)
class ServiceConfig:
    cpu: int
    memory_mib: int
    desired_count: int = 1
    min_count: int = 1
    max_count: int = 2
    container_port: int = 8000
    command: list[str] = field(default_factory=list)
    web_concurrency: int | None = None
    requests_per_target: int | None = None
    queue_scale_up_depth: int | None = None
    queue_scale_down_depth: int | None = None
    domain_name: str | None = None
    certificate_arn: str | None = None
    hosted_zone_name: str | None = None


@dataclass(frozen=True)
class FrontendConfig:
    enabled: bool = True
    domain_name: str | None = None
    certificate_arn: str | None = None
    hosted_zone_name: str | None = None


@dataclass(frozen=True)
class EnvironmentConfig:
    app_name: str
    environment_name: str
    account: str
    region: str
    app_env: str
    log_level: str
    network: NetworkConfig
    cors_allowed_origins: list[str]
    auth_frontend_base_url: str | None
    # WebAuthn Relying Party id — the host the browser binds passkeys to.
    # Must equal or be a registrable suffix of every webauthn_allowed_origins
    # entry; passkeys registered against one RP id cannot be presented to a
    # different one. Without this set the runtime falls back to "localhost"
    # and the browser rejects registration with "RP ID is invalid".
    webauthn_rp_id: str | None
    webauthn_allowed_origins: list[str]
    recordings_bucket_name: str
    # CIDRs whose direct peers are trusted to set X-Forwarded-For. Behind an
    # ALB this should at minimum cover the ALB subnet ranges; RFC1918 is a
    # safe default for any private VPC layout.
    trusted_proxy_cidrs: list[str]
    database: DatabaseConfig
    redis: RedisConfig
    api: ServiceConfig
    worker: ServiceConfig
    frontend: FrontendConfig
    external_secrets: dict[str, str]
    optional_secrets: dict[str, str]
    # Optional email for CloudWatch alarm notifications (RDS metrics, audit
    # persistence failures, ALB 5xx). Leave empty to provision the alarms +
    # SNS topic without subscribers — operators can add a subscription
    # manually in the console without redeploying.
    alarm_email: str | None = None
    # Per-IP edge rate limit (requests / 5-minute window). Layered on top of
    # slowapi (per-process) so a flood at the edge gets blocked before it
    # touches the application. AWS default minimum is 100; raise as traffic
    # grows. 2_000 is comfortable for staging.
    waf_rate_limit_per_5min: int = 2000


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"CDK config file not found: {path}") from exc


def load_config(path: str | Path) -> EnvironmentConfig:
    config_path = Path(path)
    raw = _read_json(config_path)

    network = raw.get("network", {})
    database = raw["database"]
    redis = raw["redis"]
    api = raw["api"]
    worker = raw["worker"]
    frontend = raw.get("frontend", {})

    return EnvironmentConfig(
        app_name=raw["appName"],
        environment_name=raw["environmentName"],
        account=raw["account"],
        region=raw["region"],
        app_env=raw.get("appEnv", raw["environmentName"]),
        log_level=raw.get("logLevel", "info"),
        network=NetworkConfig(
            vpc_id=network.get("vpcId"),
            max_azs=network.get("maxAzs", 2),
            nat_gateways=network.get("natGateways", 1),
        ),
        cors_allowed_origins=list(raw.get("corsAllowedOrigins", [])),
        auth_frontend_base_url=raw.get("authFrontendBaseUrl"),
        webauthn_rp_id=raw.get("webauthnRpId"),
        webauthn_allowed_origins=list(raw.get("webauthnAllowedOrigins", [])),
        recordings_bucket_name=raw["recordingsBucketName"],
        trusted_proxy_cidrs=list(
            raw.get(
                "trustedProxyCidrs",
                ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"],
            )
        ),
        database=DatabaseConfig(
            name=database["name"],
            username=database.get("username", "nexhealth_admin"),
            instance_type=database["instanceType"],
            allocated_storage=database.get("allocatedStorage", 20),
            max_allocated_storage=database.get("maxAllocatedStorage", 100),
            multi_az=database.get("multiAz", False),
            backup_retention_days=database.get("backupRetentionDays", 7),
            deletion_protection=database.get("deletionProtection", False),
            engine_major_version=database.get("engineMajorVersion", "16"),
            engine_full_version=database.get("engineFullVersion", "16.6"),
            proxy_enabled=database.get("proxyEnabled", False),
            app_pool_size=int(database.get("appPoolSize", 5)),
            app_max_overflow=int(database.get("appMaxOverflow", 5)),
            app_pool_timeout_seconds=int(database.get("appPoolTimeoutSeconds", 10)),
            app_pool_recycle_seconds=int(database.get("appPoolRecycleSeconds", 1800)),
        ),
        redis=RedisConfig(
            node_type=redis["nodeType"],
            engine_version=redis.get("engineVersion", "7.1"),
            num_node_groups=redis.get("numNodeGroups", 1),
            replicas_per_node_group=redis.get("replicasPerNodeGroup", 0),
            automatic_failover_enabled=redis.get("automaticFailoverEnabled", False),
            multi_az_enabled=redis.get("multiAzEnabled", False),
        ),
        api=ServiceConfig(
            cpu=api["cpu"],
            memory_mib=api["memoryMiB"],
            desired_count=api.get("desiredCount", 1),
            min_count=api.get("minCount", 1),
            max_count=api.get("maxCount", 2),
            container_port=api.get("containerPort", 8000),
            web_concurrency=api.get("webConcurrency"),
            requests_per_target=api.get("requestsPerTarget"),
            domain_name=api.get("domainName"),
            certificate_arn=api.get("certificateArn"),
            hosted_zone_name=api.get("hostedZoneName"),
        ),
        worker=ServiceConfig(
            cpu=worker["cpu"],
            memory_mib=worker["memoryMiB"],
            desired_count=worker.get("desiredCount", 1),
            min_count=worker.get("minCount", 1),
            max_count=worker.get("maxCount", 2),
            command=list(worker.get("command", [])),
            queue_scale_up_depth=worker.get("queueScaleUpDepth"),
            queue_scale_down_depth=worker.get("queueScaleDownDepth"),
        ),
        frontend=FrontendConfig(
            enabled=frontend.get("enabled", True),
            domain_name=frontend.get("domainName"),
            certificate_arn=frontend.get("certificateArn"),
            hosted_zone_name=frontend.get("hostedZoneName"),
        ),
        external_secrets=dict(raw.get("externalSecrets", {})),
        optional_secrets=dict(raw.get("optionalSecrets", {})),
        alarm_email=raw.get("alarmEmail") or None,
        waf_rate_limit_per_5min=int(raw.get("wafRateLimitPer5Min", 2000)),
    )
