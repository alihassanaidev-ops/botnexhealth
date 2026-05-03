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
        ),
        frontend=FrontendConfig(
            enabled=frontend.get("enabled", True),
            domain_name=frontend.get("domainName"),
            certificate_arn=frontend.get("certificateArn"),
            hosted_zone_name=frontend.get("hostedZoneName"),
        ),
        external_secrets=dict(raw.get("externalSecrets", {})),
        optional_secrets=dict(raw.get("optionalSecrets", {})),
    )
