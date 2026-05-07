from __future__ import annotations

from pathlib import Path

from aws_cdk import (
    Annotations,
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    Tags,
    aws_certificatemanager as acm,
    aws_applicationautoscaling as appscaling,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_cloudwatch as cloudwatch,
    aws_cloudwatch_actions as cw_actions,
    aws_ec2 as ec2,
    aws_ecr_assets as ecr_assets,
    aws_ecs as ecs,
    aws_ecs_patterns as ecs_patterns,
    aws_elasticache as elasticache,
    aws_events as events,
    aws_events_targets as events_targets,
    aws_iam as iam,
    aws_logs as logs,
    aws_rds as rds,
    aws_route53 as route53,
    aws_route53_targets as route53_targets,
    aws_s3 as s3,
    aws_secretsmanager as secretsmanager,
    aws_sns as sns,
    aws_sns_subscriptions as sns_subs,
    aws_wafv2 as wafv2,
)
from constructs import Construct

from .config import EnvironmentConfig


class NexHealthPlatformStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, config: EnvironmentConfig, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.config = config

        Tags.of(self).add("app", config.app_name)
        Tags.of(self).add("environment", config.environment_name)
        Tags.of(self).add("managed-by", "cdk")

        vpc = self._build_vpc()
        private_subnets = ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS)

        cluster = ecs.Cluster(
            self,
            "Cluster",
            cluster_name=f"{config.app_name}-{config.environment_name}",
            vpc=vpc,
            container_insights_v2=ecs.ContainerInsights.ENABLED,
        )

        recordings_bucket = s3.Bucket(
            self,
            "RecordingsBucket",
            bucket_name=config.recordings_bucket_name,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=True,
            removal_policy=RemovalPolicy.RETAIN,
            auto_delete_objects=False,
            lifecycle_rules=[
                # Recordings cost is otherwise unbounded. Move warm-rare data
                # off Standard quickly (call recordings are accessed at most
                # a handful of times via the audited reveal endpoint, then
                # almost never). Net saving — no upfront cost.
                s3.LifecycleRule(
                    id="recordings-tiering",
                    enabled=True,
                    transitions=[
                        s3.Transition(
                            storage_class=s3.StorageClass.INFREQUENT_ACCESS,
                            transition_after=Duration.days(90),
                        ),
                        s3.Transition(
                            storage_class=s3.StorageClass.DEEP_ARCHIVE,
                            transition_after=Duration.days(365),
                        ),
                    ],
                    abort_incomplete_multipart_upload_after=Duration.days(7),
                    noncurrent_version_expiration=Duration.days(30),
                ),
            ],
        )

        # VPC Gateway Endpoint for S3 — free, removes NAT egress charges for
        # recording uploads/downloads. Routes S3 traffic through a private
        # endpoint instead of out via the NAT gateway.
        if not config.network.vpc_id:
            # add_gateway_endpoint only works on VPCs we created. For
            # imported VPCs the endpoint must be added at the VPC level
            # outside this stack.
            vpc.add_gateway_endpoint(
                "S3GatewayEndpoint",
                service=ec2.GatewayVpcEndpointAwsService.S3,
            )

        frontend_bundle = self._build_frontend()
        frontend_base_url = config.auth_frontend_base_url or frontend_bundle["url"]

        jwt_secret = secretsmanager.Secret(
            self,
            "JwtSecret",
            secret_name=f"{config.app_name}/{config.environment_name}/jwt-secret",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                exclude_punctuation=True,
                password_length=64,
            ),
        )
        encryption_key_secret = secretsmanager.Secret(
            self,
            "EncryptionKeySecret",
            secret_name=f"{config.app_name}/{config.environment_name}/encryption-key",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                # 43 base64-compatible chars decode to exactly 32 bytes once
                # padded by the app, giving AES-256 key material without
                # embedding plaintext secrets in the synthesized template.
                exclude_punctuation=True,
                password_length=43,
            ),
        )

        db_security_group = ec2.SecurityGroup(
            self,
            "DatabaseSecurityGroup",
            vpc=vpc,
            allow_all_outbound=False,
            description="PostgreSQL access for ECS services",
        )

        # Force SSL for every connection to RDS. HIPAA §164.312(e) requires
        # encryption-in-transit for ePHI; without this parameter group RDS
        # accepts cleartext connections from the VPC. force_ssl is a static
        # parameter so attaching this on an existing DB triggers a reboot.
        db_engine = rds.DatabaseInstanceEngine.postgres(
            version=rds.PostgresEngineVersion.of(
                config.database.engine_full_version,
                config.database.engine_major_version,
            )
        )
        db_parameter_group = rds.ParameterGroup(
            self,
            "DatabaseParameters",
            engine=db_engine,
            description=f"{config.app_name}-{config.environment_name} force-SSL params",
            parameters={
                "rds.force_ssl": "1",
                # Log connections / disconnections at low cost — useful for
                # forensic incident review.
                "log_connections": "1",
                "log_disconnections": "1",
            },
        )

        database = rds.DatabaseInstance(
            self,
            "Database",
            engine=db_engine,
            instance_type=ec2.InstanceType(config.database.instance_type),
            credentials=rds.Credentials.from_generated_secret(
                config.database.username,
                secret_name=f"{config.app_name}/{config.environment_name}/database-master",
            ),
            database_name=config.database.name,
            vpc=vpc,
            vpc_subnets=private_subnets,
            security_groups=[db_security_group],
            parameter_group=db_parameter_group,
            allocated_storage=config.database.allocated_storage,
            max_allocated_storage=config.database.max_allocated_storage,
            multi_az=config.database.multi_az,
            storage_encrypted=True,
            deletion_protection=config.database.deletion_protection,
            backup_retention=Duration.days(config.database.backup_retention_days),
            removal_policy=RemovalPolicy.SNAPSHOT,
            publicly_accessible=False,
            enable_performance_insights=True,
            auto_minor_version_upgrade=True,
        )

        # Rotate the master password on a schedule. Uses the AWS-provided
        # rotation Lambda (deployed into the VPC by CDK) — invocation cost
        # is pennies/month at this cadence.
        database.add_rotation_single_user(
            automatically_after=Duration.days(60),
        )

        # Application runtime role. The RDS-generated MASTER credentials
        # have BYPASSRLS-equivalent privileges and would defeat the entire
        # row-level-security model — see migrations/policies in
        # alembic/versions/20260510_consolidated_baseline.py. Issue a
        # separate secret for a least-privilege role; the migration task
        # provisions the role and grants on first deploy (see
        # src/app/scripts/migrate_database.py).
        app_role_secret = secretsmanager.Secret(
            self,
            "AppRoleSecret",
            secret_name=f"{config.app_name}/{config.environment_name}/database-app-role",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template='{"username": "nexhealth_app"}',
                generate_string_key="password",
                exclude_punctuation=True,
                password_length=32,
            ),
        )

        migration_security_group = ec2.SecurityGroup(
            self,
            "MigrationSecurityGroup",
            vpc=vpc,
            allow_all_outbound=True,
            description="One-off ECS migration task access",
        )

        database_host_for_runtime = database.db_instance_endpoint_address
        database_proxy: rds.DatabaseProxy | None = None
        db_proxy_security_group: ec2.SecurityGroup | None = None
        if config.database.proxy_enabled:
            db_proxy_security_group = ec2.SecurityGroup(
                self,
                "DatabaseProxySecurityGroup",
                vpc=vpc,
                allow_all_outbound=True,
                description="RDS Proxy access for API and worker tasks",
            )
            database_proxy = rds.DatabaseProxy(
                self,
                "DatabaseProxy",
                proxy_target=rds.ProxyTarget.from_instance(database),
                secrets=[app_role_secret],
                vpc=vpc,
                vpc_subnets=private_subnets,
                security_groups=[db_proxy_security_group],
                require_tls=True,
                idle_client_timeout=Duration.minutes(30),
                debug_logging=False,
            )
            database_host_for_runtime = database_proxy.endpoint
            db_security_group.add_ingress_rule(
                db_proxy_security_group,
                ec2.Port.tcp(5432),
                "RDS Proxy access to PostgreSQL",
            )

        redis_security_group = ec2.SecurityGroup(
            self,
            "RedisSecurityGroup",
            vpc=vpc,
            allow_all_outbound=False,
            description="Redis access for ECS services",
        )
        redis_subnet_group = elasticache.CfnSubnetGroup(
            self,
            "RedisSubnetGroup",
            description=f"{config.app_name}-{config.environment_name} Redis subnet group",
            subnet_ids=[subnet.subnet_id for subnet in vpc.private_subnets],
            cache_subnet_group_name=f"{config.app_name}-{config.environment_name}-redis-subnets",
        )
        redis = elasticache.CfnReplicationGroup(
            self,
            "Redis",
            replication_group_description=f"{config.app_name}-{config.environment_name} Redis",
            engine="redis",
            engine_version=config.redis.engine_version,
            cache_node_type=config.redis.node_type,
            num_node_groups=config.redis.num_node_groups,
            replicas_per_node_group=config.redis.replicas_per_node_group,
            automatic_failover_enabled=config.redis.automatic_failover_enabled,
            multi_az_enabled=config.redis.multi_az_enabled,
            at_rest_encryption_enabled=True,
            transit_encryption_enabled=True,
            security_group_ids=[redis_security_group.security_group_id],
            cache_subnet_group_name=redis_subnet_group.ref,
            port=6379,
        )
        redis.add_dependency(redis_subnet_group)
        redis_url = (
            f"rediss://{redis.attr_primary_end_point_address}:{redis.attr_primary_end_point_port}/0"
            "?ssl_cert_reqs=required"
        )

        runtime_environment = self._build_runtime_environment(
            frontend_base_url,
            frontend_bundle["url"],
            redis_url,
            recordings_bucket,
            database_host_for_runtime,
            database.db_instance_endpoint_port,
        )
        runtime_secrets = self._build_app_runtime_secrets(
            jwt_secret,
            encryption_key_secret,
            app_role_secret,
        )
        migration_secrets = self._build_migration_secrets(
            jwt_secret,
            encryption_key_secret,
            database,
        )
        migration_environment = {
            **runtime_environment,
            "APP_ROLE_SECRET_ARN": app_role_secret.secret_arn,
            # Migrations use master credentials and must connect directly to
            # RDS. Runtime tasks connect through RDS Proxy when enabled.
            "DATABASE_HOST": database.db_instance_endpoint_address,
            "DATABASE_PORT": database.db_instance_endpoint_port,
        }

        app_image_asset = ecr_assets.DockerImageAsset(
            self,
            "ApplicationImage",
            directory=str(Path(__file__).resolve().parents[2]),
            file="Dockerfile",
            platform=ecr_assets.Platform.LINUX_AMD64,
        )
        app_image = ecs.ContainerImage.from_docker_image_asset(app_image_asset)

        # Explicit log groups so we can attach a metric filter for
        # "AUDIT PERSISTENCE FAILURE" and tighten retention per stream.
        api_log_group = logs.LogGroup(
            self,
            "ApiLogs",
            log_group_name=f"/{config.app_name}/{config.environment_name}/api",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.RETAIN,
        )
        worker_log_group = logs.LogGroup(
            self,
            "WorkerLogs",
            log_group_name=f"/{config.app_name}/{config.environment_name}/worker",
            retention=logs.RetentionDays.TWO_WEEKS,
            removal_policy=RemovalPolicy.RETAIN,
        )
        migration_log_group = logs.LogGroup(
            self,
            "MigrationLogs",
            log_group_name=f"/{config.app_name}/{config.environment_name}/migrations",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Audit-write failure alarm. When the @audit decorator's durable
        # post-action write fails, audit.py logs at CRITICAL with
        # "AUDIT PERSISTENCE FAILURE" — see services/audit.py:283. That log
        # line is the operator's ONLY signal that a PHI mutation committed
        # without a paired audit row, so we want it paged immediately.
        audit_failure_filter = logs.MetricFilter(
            self,
            "AuditPersistenceFailureFilter",
            log_group=api_log_group,
            filter_pattern=logs.FilterPattern.literal('"AUDIT PERSISTENCE FAILURE"'),
            metric_namespace=f"{config.app_name}/{config.environment_name}",
            metric_name="AuditPersistenceFailures",
            metric_value="1",
            default_value=0,
        )

        api_task_definition = ecs.FargateTaskDefinition(
            self,
            "ApiTaskDefinition",
            cpu=config.api.cpu,
            memory_limit_mib=config.api.memory_mib,
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.X86_64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
        )
        api_container = api_task_definition.add_container(
            "ApiContainer",
            image=app_image,
            environment=runtime_environment,
            secrets=runtime_secrets,
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="api",
                log_group=api_log_group,
            ),
            health_check=ecs.HealthCheck(
                command=["CMD-SHELL", "curl --fail http://localhost:8000/livez || exit 1"],
                interval=Duration.seconds(30),
                timeout=Duration.seconds(10),
                retries=3,
                start_period=Duration.seconds(20),
            ),
        )
        api_container.add_port_mappings(ecs.PortMapping(container_port=config.api.container_port))

        api_service = self._build_api_service(
            cluster=cluster,
            task_definition=api_task_definition,
            service_config=config.api,
            subnet_selection=private_subnets,
        )
        api_service.load_balancer.set_attribute("idle_timeout.timeout_seconds", "300")
        api_service.target_group.configure_health_check(path="/livez", healthy_http_codes="200")

        if hasattr(self, "frontend_distribution"):
            # CloudFront → ALB origin protocol must match the ALB listener.
            # When the API has domain + cert + zone, _build_api_service
            # provisions an HTTPS-only listener on 443; we MUST then talk
            # HTTPS to the origin so ePHI transport is encrypted end-to-end
            # (HIPAA §164.312(e)). Without a domain the ALB can only listen
            # on HTTP/80, and CloudFront has no public CA chain to validate
            # an AWS-managed *.elb.amazonaws.com cert against — that path is
            # NOT acceptable for PHI traffic and is gated below.
            alb_has_https = bool(
                config.api.domain_name
                and config.api.certificate_arn
                and config.api.hosted_zone_name
            )
            origin_protocol = (
                cloudfront.OriginProtocolPolicy.HTTPS_ONLY
                if alb_has_https
                else cloudfront.OriginProtocolPolicy.HTTP_ONLY
            )
            if not alb_has_https:
                Annotations.of(self).add_warning(
                    "API ALB has no HTTPS listener (api.domainName / certificateArn "
                    "/ hostedZoneName missing). CloudFront → ALB will be HTTP. "
                    "This deployment is NOT HIPAA-compliant for ePHI transport. "
                    "Use synthetic data only until a domain + ACM certificate "
                    "are configured for the API service."
                )

            api_origin_domain = (
                config.api.domain_name
                if alb_has_https
                else api_service.load_balancer.load_balancer_dns_name
            )
            api_origin_request_policy = (
                cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER
                if alb_has_https
                else cloudfront.OriginRequestPolicy.ALL_VIEWER
            )
            alb_origin = origins.HttpOrigin(
                api_origin_domain,
                protocol_policy=origin_protocol,
            )
            # SSE endpoint — longer origin read timeout for streaming
            self.frontend_distribution.add_behavior(
                "/api/institution/events*",
                origins.HttpOrigin(
                    api_origin_domain,
                    protocol_policy=origin_protocol,
                    read_timeout=Duration.seconds(60),
                ),
                allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                origin_request_policy=api_origin_request_policy,
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
            )
            # All other API requests
            self.frontend_distribution.add_behavior(
                "/api/*",
                alb_origin,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                origin_request_policy=api_origin_request_policy,
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
            )

        api_scaling = api_service.service.auto_scale_task_count(
            min_capacity=config.api.min_count,
            max_capacity=config.api.max_count,
        )
        api_scaling.scale_on_cpu_utilization("ApiCpuScaling", target_utilization_percent=65)
        api_scaling.scale_on_memory_utilization("ApiMemoryScaling", target_utilization_percent=75)
        if config.api.requests_per_target:
            api_scaling.scale_on_request_count(
                "ApiRequestCountScaling",
                requests_per_target=config.api.requests_per_target,
                target_group=api_service.target_group,
            )

        worker_task_definition = ecs.FargateTaskDefinition(
            self,
            "WorkerTaskDefinition",
            cpu=config.worker.cpu,
            memory_limit_mib=config.worker.memory_mib,
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.X86_64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
        )
        worker_task_definition.add_container(
            "WorkerContainer",
            image=app_image,
            command=config.worker.command,
            environment=runtime_environment,
            secrets=runtime_secrets,
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="worker",
                log_group=worker_log_group,
            ),
            health_check=ecs.HealthCheck(
                command=["CMD-SHELL", "celery -A src.app.worker inspect ping || exit 1"],
                interval=Duration.seconds(30),
                timeout=Duration.seconds(10),
                retries=3,
                start_period=Duration.seconds(30),
            ),
        )
        worker_service = ecs.FargateService(
            self,
            "WorkerService",
            cluster=cluster,
            task_definition=worker_task_definition,
            desired_count=config.worker.desired_count,
            assign_public_ip=False,
            enable_execute_command=True,
            vpc_subnets=private_subnets,
            circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=True),
        )
        worker_scaling = worker_service.auto_scale_task_count(
            min_capacity=config.worker.min_count,
            max_capacity=config.worker.max_count,
        )
        worker_scaling.scale_on_cpu_utilization("WorkerCpuScaling", target_utilization_percent=65)
        worker_scaling.scale_on_memory_utilization("WorkerMemoryScaling", target_utilization_percent=75)
        if config.worker.queue_scale_up_depth is not None:
            queue_metrics_security_group = ec2.SecurityGroup(
                self,
                "QueueMetricsSecurityGroup",
                vpc=vpc,
                allow_all_outbound=True,
                description="Scheduled queue-depth metric publisher access",
            )
            queue_metrics_task_definition = ecs.FargateTaskDefinition(
                self,
                "QueueMetricsTaskDefinition",
                cpu=256,
                memory_limit_mib=512,
                runtime_platform=ecs.RuntimePlatform(
                    cpu_architecture=ecs.CpuArchitecture.X86_64,
                    operating_system_family=ecs.OperatingSystemFamily.LINUX,
                ),
            )
            queue_metrics_task_definition.add_container(
                "QueueMetricsContainer",
                image=app_image,
                command=["python", "-m", "src.app.scripts.publish_queue_metrics"],
                environment={
                    "APP_NAME": config.app_name,
                    "APP_ENV": config.app_env,
                    "AWS_REGION": config.region,
                    "REDIS_URL": redis_url,
                    "CELERY_BROKER_URL": redis_url,
                    "CELERY_QUEUE_DEPTH_NAMES": "notifications_default,notifications_high",
                },
                logging=ecs.LogDrivers.aws_logs(
                    stream_prefix="queue-metrics",
                    log_group=worker_log_group,
                ),
            )
            queue_metrics_task_definition.task_role.add_to_principal_policy(
                iam.PolicyStatement(
                    actions=["cloudwatch:PutMetricData"],
                    resources=["*"],
                    conditions={
                        "StringEquals": {
                            "cloudwatch:namespace": f"{config.app_name}/{config.app_env}"
                        }
                    },
                )
            )
            redis_security_group.add_ingress_rule(
                queue_metrics_security_group,
                ec2.Port.tcp(6379),
                "Queue metric publisher access to Redis",
            )
            events.Rule(
                self,
                "QueueMetricsSchedule",
                schedule=events.Schedule.rate(Duration.minutes(1)),
                targets=[
                    events_targets.EcsTask(
                        cluster=cluster,
                        task_definition=queue_metrics_task_definition,
                        subnet_selection=private_subnets,
                        security_groups=[queue_metrics_security_group],
                    )
                ],
            )
            queue_depth_metric = cloudwatch.Metric(
                namespace=f"{config.app_name}/{config.app_env}",
                metric_name="CeleryQueueDepth",
                dimensions_map={"Queue": "all"},
                statistic="Average",
                period=Duration.minutes(1),
            )
            scaling_steps = []
            if config.worker.queue_scale_down_depth is not None:
                scaling_steps.append(
                    appscaling.ScalingInterval(
                        upper=config.worker.queue_scale_down_depth,
                        change=-1,
                    )
                )
            scaling_steps.extend(
                [
                    appscaling.ScalingInterval(
                        lower=config.worker.queue_scale_up_depth,
                        change=1,
                    ),
                    appscaling.ScalingInterval(
                        lower=config.worker.queue_scale_up_depth * 3,
                        change=2,
                    ),
                ]
            )
            worker_scaling.scale_on_metric(
                "WorkerQueueDepthScaling",
                metric=queue_depth_metric,
                scaling_steps=scaling_steps,
                adjustment_type=appscaling.AdjustmentType.CHANGE_IN_CAPACITY,
                cooldown=Duration.minutes(2),
            )

        migration_task_definition = ecs.FargateTaskDefinition(
            self,
            "MigrationTaskDefinition",
            cpu=512,
            memory_limit_mib=1024,
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.X86_64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
        )
        migration_task_definition.add_container(
            "MigrationContainer",
            image=app_image,
            command=["python", "-m", "src.app.scripts.migrate_database"],
            environment=migration_environment,
            secrets=migration_secrets,
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="migrations",
                log_group=migration_log_group,
            ),
        )
        # Migration task reads the master creds (via secret bindings above)
        # AND must read the app-role secret to seed its initial password.
        # API + worker tasks are NOT granted access to the master secret —
        # only to the app-role secret (via _build_app_runtime_secrets).
        app_role_secret.grant_read(migration_task_definition.task_role)

        recordings_bucket.grant_read_write(api_task_definition.task_role)
        recordings_bucket.grant_read_write(worker_task_definition.task_role)
        recordings_bucket.grant_read_write(migration_task_definition.task_role)

        # ── Scheduled admin background jobs ─────────────────────────────
        # Periodic ECS RunTask invocations triggered by EventBridge.
        # Each one runs as the database master role (NOT the runtime
        # ``nexhealth_app`` role) because they perform cross-tenant work
        # that must bypass RLS. Same image as the migration task; only
        # the entrypoint command differs.
        scheduled_jobs_log_group = logs.LogGroup(
            self,
            "ScheduledJobsLogs",
            log_group_name=f"/{config.app_name}/{config.environment_name}/scheduled-jobs",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.RETAIN,
        )

        # Recompute the dashboard rollup every 5 minutes.
        # ``call_metrics_daily`` is the load-bearing table behind every
        # dashboard volume card past a few thousand calls per institution.
        # The rollup excludes today (caller adds it live), so a 5-min
        # cadence keeps yesterday's totals tight without expensive scans.
        recompute_rollup_task = self._build_scheduled_admin_task(
            id_prefix="RecomputeDashboardRollup",
            command=[
                "python",
                "-m",
                "src.app.scripts.recompute_dashboard_rollup",
            ],
            log_group=scheduled_jobs_log_group,
            log_stream_prefix="rollup",
            image=app_image,
            environment=migration_environment,
            secrets=migration_secrets,
            vpc=vpc,
            cluster=cluster,
            db_security_group=db_security_group,
            private_subnets=private_subnets,
            schedule=events.Schedule.rate(Duration.minutes(5)),
        )

        # Ensure audit_logs has the next N monthly partitions pre-created
        # so any INSERT lands in a real partition instead of the DEFAULT
        # catch-all (where queries lose partition pruning). Daily at
        # 02:30 UTC — comfortably ahead of the cleanup task at 03:00.
        ensure_audit_partitions_task = self._build_scheduled_admin_task(
            id_prefix="EnsureAuditPartitions",
            command=[
                "python",
                "-m",
                "src.app.scripts.ensure_audit_partitions",
            ],
            log_group=scheduled_jobs_log_group,
            log_stream_prefix="audit-partitions",
            image=app_image,
            environment=migration_environment,
            secrets=migration_secrets,
            vpc=vpc,
            cluster=cluster,
            db_security_group=db_security_group,
            private_subnets=private_subnets,
            schedule=events.Schedule.cron(hour="2", minute="30"),
        )

        # Prune idempotency tables once a day.
        # Without this ``retell_function_invocations`` /
        # ``retell_webhook_events`` / ``dead_letter_events`` grow
        # unbounded — at ~36M rows/year per table for a busy tenant
        # they'd dominate INSERT cost on the call/webhook hot path.
        # 03:00 UTC is the canonical low-traffic window for North
        # American clinics.
        cleanup_idempotency_task = self._build_scheduled_admin_task(
            id_prefix="CleanupIdempotency",
            command=["python", "-m", "src.app.scripts.cleanup_idempotency"],
            log_group=scheduled_jobs_log_group,
            log_stream_prefix="cleanup-idempotency",
            image=app_image,
            environment=migration_environment,
            secrets=migration_secrets,
            vpc=vpc,
            cluster=cluster,
            db_security_group=db_security_group,
            private_subnets=private_subnets,
            schedule=events.Schedule.cron(hour="3", minute="0"),
        )

        CfnOutput(
            self,
            "RecomputeDashboardRollupTaskArn",
            value=recompute_rollup_task.task_definition_arn,
        )
        CfnOutput(
            self,
            "EnsureAuditPartitionsTaskArn",
            value=ensure_audit_partitions_task.task_definition_arn,
        )
        CfnOutput(
            self,
            "CleanupIdempotencyTaskArn",
            value=cleanup_idempotency_task.task_definition_arn,
        )

        if db_proxy_security_group is not None:
            db_proxy_security_group.add_ingress_rule(
                api_service.service.connections.security_groups[0],
                ec2.Port.tcp(5432),
                "API access to RDS Proxy",
            )
            db_proxy_security_group.add_ingress_rule(
                worker_service.connections.security_groups[0],
                ec2.Port.tcp(5432),
                "Worker access to RDS Proxy",
            )
        else:
            db_security_group.add_ingress_rule(
                api_service.service.connections.security_groups[0],
                ec2.Port.tcp(5432),
                "API access to PostgreSQL",
            )
            db_security_group.add_ingress_rule(
                worker_service.connections.security_groups[0],
                ec2.Port.tcp(5432),
                "Worker access to PostgreSQL",
            )
        db_security_group.add_ingress_rule(
            migration_security_group,
            ec2.Port.tcp(5432),
            "Migration task access to PostgreSQL",
        )
        redis_security_group.add_ingress_rule(
            api_service.service.connections.security_groups[0],
            ec2.Port.tcp(6379),
            "API access to Redis",
        )
        redis_security_group.add_ingress_rule(
            worker_service.connections.security_groups[0],
            ec2.Port.tcp(6379),
            "Worker access to Redis",
        )

        self._attach_waf(api_service.load_balancer.load_balancer_arn)
        self._attach_alarms(
            api_service=api_service,
            database=database,
            audit_failure_filter=audit_failure_filter,
        )
        api_url = self._api_base_url(api_service)

        CfnOutput(self, "ClusterName", value=cluster.cluster_name)
        CfnOutput(self, "ApiBaseUrl", value=api_url)
        CfnOutput(self, "AppSecurityGroupId", value=api_service.service.connections.security_groups[0].security_group_id)
        CfnOutput(self, "MigrationSecurityGroupId", value=migration_security_group.security_group_id)
        CfnOutput(
            self,
            "PrivateSubnetIds",
            value=",".join(subnet.subnet_id for subnet in vpc.private_subnets),
        )
        CfnOutput(self, "MigrationTaskDefinitionArn", value=migration_task_definition.task_definition_arn)
        CfnOutput(self, "RecordingsBucketName", value=recordings_bucket.bucket_name)
        CfnOutput(self, "RedisUrl", value=redis_url)
        CfnOutput(self, "DatabaseEndpointAddress", value=database.db_instance_endpoint_address)
        if database_proxy is not None:
            CfnOutput(self, "DatabaseProxyEndpoint", value=database_proxy.endpoint)
        CfnOutput(self, "DatabasePort", value=database.db_instance_endpoint_port)
        CfnOutput(
            self,
            "DatabaseConnectionTemplate",
            value=(
                "postgresql+asyncpg://<DATABASE_USER>:<DATABASE_PASSWORD>@"
                f"{database_host_for_runtime}:{database.db_instance_endpoint_port}/{config.database.name}"
            ),
        )
        if database.secret is not None:
            CfnOutput(self, "DatabaseCredentialsSecretArn", value=database.secret.secret_arn)
        CfnOutput(self, "GeneratedJwtSecretArn", value=jwt_secret.secret_arn)
        CfnOutput(self, "GeneratedEncryptionKeySecretArn", value=encryption_key_secret.secret_arn)
        if frontend_bundle["bucket_name"]:
            CfnOutput(self, "FrontendBucketName", value=frontend_bundle["bucket_name"])
        if frontend_bundle["distribution_id"]:
            CfnOutput(self, "FrontendDistributionId", value=frontend_bundle["distribution_id"])
            CfnOutput(self, "FrontendUrl", value=frontend_bundle["url"])

    def _build_vpc(self) -> ec2.IVpc:
        if self.config.network.vpc_id:
            return ec2.Vpc.from_lookup(self, "Vpc", vpc_id=self.config.network.vpc_id)

        return ec2.Vpc(
            self,
            "Vpc",
            max_azs=self.config.network.max_azs,
            nat_gateways=self.config.network.nat_gateways,
        )

    def _build_runtime_environment(
        self,
        frontend_base_url: str | None,
        frontend_bundle_url: str | None,
        redis_url: str,
        recordings_bucket: s3.Bucket,
        database_host: str,
        database_port: str,
    ) -> dict[str, str]:
        cors_origins = list(self.config.cors_allowed_origins)
        if frontend_bundle_url and frontend_bundle_url not in cors_origins:
            cors_origins.append(frontend_bundle_url)

        environment = {
            "APP_ENV": self.config.app_env,
            "LOG_LEVEL": self.config.log_level,
            "AWS_REGION": self.config.region,
            "AWS_S3_BUCKET_NAME": recordings_bucket.bucket_name,
            "REDIS_URL": redis_url,
            "CELERY_BROKER_URL": redis_url,
            "DATABASE_HOST": database_host,
            "DATABASE_PORT": database_port,
            "DATABASE_NAME": self.config.database.name,
            "DATABASE_POOL_SIZE": str(self.config.database.app_pool_size),
            "DATABASE_MAX_OVERFLOW": str(self.config.database.app_max_overflow),
            "DATABASE_POOL_TIMEOUT_SECONDS": str(
                self.config.database.app_pool_timeout_seconds
            ),
            "DATABASE_POOL_RECYCLE_SECONDS": str(
                self.config.database.app_pool_recycle_seconds
            ),
        }
        if self.config.api.web_concurrency:
            environment["WEB_CONCURRENCY"] = str(self.config.api.web_concurrency)
        if cors_origins:
            environment["CORS_ALLOWED_ORIGINS"] = ",".join(cors_origins)
        if frontend_base_url:
            environment["AUTH_FRONTEND_BASE_URL"] = frontend_base_url
        if self.config.trusted_proxy_cidrs:
            environment["TRUSTED_PROXY_CIDRS"] = ",".join(self.config.trusted_proxy_cidrs)
        return environment

    def _build_app_runtime_secrets(
        self,
        jwt_secret: secretsmanager.Secret,
        encryption_key_secret: secretsmanager.Secret,
        app_role_secret: secretsmanager.Secret,
    ) -> dict[str, ecs.Secret]:
        """Secrets for API + worker tasks.

        DATABASE_USER / DATABASE_PASSWORD point at the least-privilege
        nexhealth_app role, NOT the RDS master. The master credentials
        are deliberately not reachable from runtime — using master
        bypasses RLS and defeats the multi-tenant isolation model.
        """
        secrets: dict[str, ecs.Secret] = {
            "JWT_SECRET": ecs.Secret.from_secrets_manager(jwt_secret),
            "ENCRYPTION_KEY": ecs.Secret.from_secrets_manager(encryption_key_secret),
            "DATABASE_USER": ecs.Secret.from_secrets_manager(app_role_secret, "username"),
            "DATABASE_PASSWORD": ecs.Secret.from_secrets_manager(app_role_secret, "password"),
        }
        return self._add_external_secrets(secrets, "AppRuntime")

    def _build_migration_secrets(
        self,
        jwt_secret: secretsmanager.Secret,
        encryption_key_secret: secretsmanager.Secret,
        database: rds.DatabaseInstance,
    ) -> dict[str, ecs.Secret]:
        """Secrets for the one-off migration task.

        Migrations run as the RDS master so they can ALTER schema and
        provision/rotate the runtime role. Only the migration task
        receives master credentials.
        """
        secrets: dict[str, ecs.Secret] = {
            "JWT_SECRET": ecs.Secret.from_secrets_manager(jwt_secret),
            "ENCRYPTION_KEY": ecs.Secret.from_secrets_manager(encryption_key_secret),
        }
        if database.secret is not None:
            secrets["DATABASE_USER"] = ecs.Secret.from_secrets_manager(database.secret, "username")
            secrets["DATABASE_PASSWORD"] = ecs.Secret.from_secrets_manager(database.secret, "password")
        return self._add_external_secrets(secrets, "Migration")

    def _add_external_secrets(
        self,
        secrets: dict[str, ecs.Secret],
        scope_id: str,
    ) -> dict[str, ecs.Secret]:
        for env_name, secret_arn in self.config.external_secrets.items():
            secret = secretsmanager.Secret.from_secret_complete_arn(
                self,
                f"{scope_id}{env_name.title().replace('_', '')}ImportedSecret",
                secret_arn,
            )
            secrets[env_name] = ecs.Secret.from_secrets_manager(secret)
        return secrets

    def _build_scheduled_admin_task(
        self,
        *,
        id_prefix: str,
        command: list[str],
        log_group: logs.LogGroup,
        log_stream_prefix: str,
        image: ecs.ContainerImage,
        environment: dict[str, str],
        secrets: dict[str, ecs.Secret],
        vpc: ec2.IVpc,
        cluster: ecs.Cluster,
        db_security_group: ec2.SecurityGroup,
        private_subnets: ec2.SubnetSelection,
        schedule: events.Schedule,
    ) -> ecs.FargateTaskDefinition:
        """Provision an EventBridge → ECS RunTask schedule for an admin job.

        Each scheduled admin job is one Fargate task definition + one
        EventBridge rule. Tasks share the same Docker image as the API
        and migration tasks; only the ``command`` differs. They use the
        migration secrets (database master credentials) because they
        operate cross-tenant and must bypass RLS.

        ``id_prefix`` becomes part of every CDK construct id so a synth
        diff makes it obvious which job changed (e.g.
        ``RecomputeDashboardRollupTaskDefinition``).
        """
        security_group = ec2.SecurityGroup(
            self,
            f"{id_prefix}SecurityGroup",
            vpc=vpc,
            allow_all_outbound=True,
            description=f"Scheduled admin job: {id_prefix}",
        )
        db_security_group.add_ingress_rule(
            security_group,
            ec2.Port.tcp(5432),
            f"{id_prefix} access to PostgreSQL",
        )

        task_definition = ecs.FargateTaskDefinition(
            self,
            f"{id_prefix}TaskDefinition",
            cpu=256,
            memory_limit_mib=512,
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.X86_64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
        )
        task_definition.add_container(
            f"{id_prefix}Container",
            image=image,
            command=command,
            environment=environment,
            secrets=secrets,
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix=log_stream_prefix,
                log_group=log_group,
            ),
        )

        events.Rule(
            self,
            f"{id_prefix}Schedule",
            schedule=schedule,
            targets=[
                events_targets.EcsTask(
                    cluster=cluster,
                    task_definition=task_definition,
                    subnet_selection=private_subnets,
                    security_groups=[security_group],
                )
            ],
        )

        return task_definition

    def _build_api_service(
        self,
        *,
        cluster: ecs.Cluster,
        task_definition: ecs.FargateTaskDefinition,
        service_config,
        subnet_selection: ec2.SubnetSelection,
    ) -> ecs_patterns.ApplicationLoadBalancedFargateService:
        common_kwargs = {
            "cluster": cluster,
            "task_definition": task_definition,
            "desired_count": service_config.desired_count,
            "public_load_balancer": True,
            "enable_execute_command": True,
            "assign_public_ip": False,
            "task_subnets": subnet_selection,
            "circuit_breaker": ecs.DeploymentCircuitBreaker(rollback=True),
        }

        if service_config.domain_name and service_config.certificate_arn and service_config.hosted_zone_name:
            zone = route53.HostedZone.from_lookup(
                self,
                "ApiHostedZone",
                domain_name=service_config.hosted_zone_name,
            )
            certificate = acm.Certificate.from_certificate_arn(
                self,
                "ApiCertificate",
                service_config.certificate_arn,
            )
            return ecs_patterns.ApplicationLoadBalancedFargateService(
                self,
                "ApiService",
                **common_kwargs,
                domain_name=service_config.domain_name,
                domain_zone=zone,
                certificate=certificate,
                listener_port=443,
                redirect_http=True,
            )

        if self.config.app_env.lower() in {"production", "prod"}:
            raise ValueError(
                "Production API deployments require domainName, certificateArn, and hostedZoneName "
                "so the load balancer is exposed over HTTPS only."
            )

        return ecs_patterns.ApplicationLoadBalancedFargateService(
            self,
            "ApiService",
            **common_kwargs,
            listener_port=80,
        )

    def _build_frontend(self) -> dict[str, str]:
        if not self.config.frontend.enabled:
            return {"bucket_name": "", "distribution_id": "", "url": ""}

        site_bucket = s3.Bucket(
            self,
            "FrontendBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            versioned=True,
            removal_policy=RemovalPolicy.RETAIN,
            auto_delete_objects=False,
        )
        distribution_kwargs = {
            "default_root_object": "index.html",
            "default_behavior": cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(site_bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            ),
            "error_responses": [
                cloudfront.ErrorResponse(
                    http_status=403,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=Duration.minutes(1),
                ),
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=Duration.minutes(1),
                ),
            ],
        }

        if self.config.frontend.domain_name and self.config.frontend.certificate_arn:
            distribution_kwargs["domain_names"] = [self.config.frontend.domain_name]
            distribution_kwargs["certificate"] = acm.Certificate.from_certificate_arn(
                self,
                "FrontendCertificate",
                self.config.frontend.certificate_arn,
            )

        distribution = cloudfront.Distribution(self, "FrontendDistribution", **distribution_kwargs)
        self.frontend_distribution = distribution

        if self.config.frontend.domain_name and self.config.frontend.hosted_zone_name:
            hosted_zone = route53.HostedZone.from_lookup(
                self,
                "FrontendHostedZone",
                domain_name=self.config.frontend.hosted_zone_name,
            )
            route53.ARecord(
                self,
                "FrontendAliasRecord",
                zone=hosted_zone,
                record_name=self.config.frontend.domain_name,
                target=route53.RecordTarget.from_alias(route53_targets.CloudFrontTarget(distribution)),
            )
            route53.AaaaRecord(
                self,
                "FrontendAliasRecordIpv6",
                zone=hosted_zone,
                record_name=self.config.frontend.domain_name,
                target=route53.RecordTarget.from_alias(route53_targets.CloudFrontTarget(distribution)),
            )

        frontend_url = (
            f"https://{self.config.frontend.domain_name}"
            if self.config.frontend.domain_name
            else f"https://{distribution.distribution_domain_name}"
        )
        return {
            "bucket_name": site_bucket.bucket_name,
            "distribution_id": distribution.distribution_id,
            "url": frontend_url,
        }

    def _attach_waf(self, resource_arn: str) -> None:
        web_acl = wafv2.CfnWebACL(
            self,
            "ApiWebAcl",
            scope="REGIONAL",
            default_action={"allow": {}},
            visibility_config={
                "cloudWatchMetricsEnabled": True,
                "metricName": f"{self.config.app_name}-{self.config.environment_name}-api-waf",
                "sampledRequestsEnabled": True,
            },
            rules=[
                # Bypass WAF for HMAC-signed webhook paths. Retell and
                # Twilio sign every request with a shared secret; the
                # application verifies the signature before any handler
                # runs, so WAF inspection is redundant and routinely
                # false-positives on legitimate large payloads
                # (transcripts, structured args). WAF terminates on the
                # first matching rule, so an Allow here short-circuits
                # the managed rule sets and the rate limiter for these
                # paths only.
                {
                    "name": "AllowSignedWebhooks",
                    "priority": 0,
                    "action": {"allow": {}},
                    "statement": {
                        "orStatement": {
                            "statements": [
                                {
                                    "byteMatchStatement": {
                                        "searchString": "/api/v1/retell/",
                                        "fieldToMatch": {"uriPath": {}},
                                        "textTransformations": [
                                            {"priority": 0, "type": "NONE"}
                                        ],
                                        "positionalConstraint": "STARTS_WITH",
                                    }
                                },
                                {
                                    "byteMatchStatement": {
                                        "searchString": "/api/v1/twilio/webhooks/",
                                        "fieldToMatch": {"uriPath": {}},
                                        "textTransformations": [
                                            {"priority": 0, "type": "NONE"}
                                        ],
                                        "positionalConstraint": "STARTS_WITH",
                                    }
                                },
                            ]
                        }
                    },
                    "visibilityConfig": {
                        "cloudWatchMetricsEnabled": True,
                        "metricName": "allow-signed-webhooks",
                        "sampledRequestsEnabled": True,
                    },
                },
                {
                    "name": "AWSManagedRulesCommonRuleSet",
                    "priority": 1,
                    "overrideAction": {"none": {}},
                    "statement": {
                        "managedRuleGroupStatement": {
                            "vendorName": "AWS",
                            "name": "AWSManagedRulesCommonRuleSet",
                        }
                    },
                    "visibilityConfig": {
                        "cloudWatchMetricsEnabled": True,
                        "metricName": "common-rules",
                        "sampledRequestsEnabled": True,
                    },
                },
                {
                    "name": "AWSManagedRulesKnownBadInputsRuleSet",
                    "priority": 2,
                    "overrideAction": {"none": {}},
                    "statement": {
                        "managedRuleGroupStatement": {
                            "vendorName": "AWS",
                            "name": "AWSManagedRulesKnownBadInputsRuleSet",
                        }
                    },
                    "visibilityConfig": {
                        "cloudWatchMetricsEnabled": True,
                        "metricName": "known-bad-inputs",
                        "sampledRequestsEnabled": True,
                    },
                },
                # Per-IP edge rate limit. slowapi inside the app handles
                # per-process limits; this stops floods at the WAF before
                # they reach Fargate. AWS counts requests over a rolling
                # 5-minute window per source IP.
                {
                    "name": "RateLimitPerIp",
                    "priority": 3,
                    "action": {"block": {}},
                    "statement": {
                        "rateBasedStatement": {
                            "limit": self.config.waf_rate_limit_per_5min,
                            "aggregateKeyType": "IP",
                        }
                    },
                    "visibilityConfig": {
                        "cloudWatchMetricsEnabled": True,
                        "metricName": "rate-limit-per-ip",
                        "sampledRequestsEnabled": True,
                    },
                },
            ],
        )
        wafv2.CfnWebACLAssociation(
            self,
            "ApiWebAclAssociation",
            resource_arn=resource_arn,
            web_acl_arn=web_acl.attr_arn,
        )

    def _attach_alarms(
        self,
        *,
        api_service: ecs_patterns.ApplicationLoadBalancedFargateService,
        database: rds.DatabaseInstance,
        audit_failure_filter: logs.MetricFilter,
    ) -> None:
        """Provision a SNS topic + CloudWatch alarms.

        Free-tier-eligible: SNS topic creation, the first 10 alarms, and
        the metric filters used here are all $0/mo at staging volumes. If
        ``alarm_email`` is unset, the alarms still get created — operators
        can subscribe later from the console without redeploying.
        """
        topic = sns.Topic(
            self,
            "AlarmTopic",
            display_name=f"{self.config.app_name}-{self.config.environment_name}-alarms",
        )
        if self.config.alarm_email:
            topic.add_subscription(sns_subs.EmailSubscription(self.config.alarm_email))

        action = cw_actions.SnsAction(topic)

        # 1. Audit-write failure — the §164.312(b) tripwire. ANY occurrence
        #    means a PHI side-effect committed without a paired audit row.
        audit_alarm = cloudwatch.Alarm(
            self,
            "AuditPersistenceFailureAlarm",
            metric=audit_failure_filter.metric(
                statistic="Sum", period=Duration.minutes(5)
            ),
            threshold=1,
            evaluation_periods=1,
            datapoints_to_alarm=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description=(
                "Audit row failed to persist for a durable action. PHI may have "
                "been mutated/revealed without a matching audit_logs entry — "
                "reconcile via the request_id in the CRITICAL log line."
            ),
        )
        audit_alarm.add_alarm_action(action)

        # 2. RDS CPU sustained.
        rds_cpu = cloudwatch.Alarm(
            self,
            "RdsHighCpuAlarm",
            metric=database.metric_cpu_utilization(period=Duration.minutes(5)),
            threshold=80,
            evaluation_periods=3,
            datapoints_to_alarm=3,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description="RDS CPU > 80% for 15 minutes",
        )
        rds_cpu.add_alarm_action(action)

        # 3. RDS free storage low (< 2 GB). Catches before the DB locks up.
        rds_storage = cloudwatch.Alarm(
            self,
            "RdsLowStorageAlarm",
            metric=database.metric_free_storage_space(period=Duration.minutes(5)),
            threshold=2 * 1024 * 1024 * 1024,
            evaluation_periods=2,
            datapoints_to_alarm=2,
            comparison_operator=cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description="RDS free storage < 2 GB",
        )
        rds_storage.add_alarm_action(action)

        # 4. ALB 5xx rate — backend errors visible to clients.
        alb_5xx_metric = cloudwatch.Metric(
            namespace="AWS/ApplicationELB",
            metric_name="HTTPCode_Target_5XX_Count",
            dimensions_map={
                "LoadBalancer": api_service.load_balancer.load_balancer_full_name,
            },
            period=Duration.minutes(5),
            statistic="Sum",
        )
        alb_5xx = cloudwatch.Alarm(
            self,
            "Alb5xxAlarm",
            metric=alb_5xx_metric,
            threshold=10,
            evaluation_periods=2,
            datapoints_to_alarm=2,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description="ALB 5xx > 10 in 5 minutes (sustained)",
        )
        alb_5xx.add_alarm_action(action)

        CfnOutput(self, "AlarmTopicArn", value=topic.topic_arn)

    def _api_base_url(self, api_service: ecs_patterns.ApplicationLoadBalancedFargateService) -> str:
        if self.config.api.domain_name and self.config.api.certificate_arn:
            return f"https://{self.config.api.domain_name}"
        return f"http://{api_service.load_balancer.load_balancer_dns_name}"
