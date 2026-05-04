from __future__ import annotations

from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    Tags,
    aws_certificatemanager as acm,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_cloudwatch as cloudwatch,
    aws_cloudwatch_actions as cw_actions,
    aws_ec2 as ec2,
    aws_ecr_assets as ecr_assets,
    aws_ecs as ecs,
    aws_ecs_patterns as ecs_patterns,
    aws_elasticache as elasticache,
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
            database,
        )
        runtime_secrets = self._build_runtime_secrets(
            jwt_secret,
            encryption_key_secret,
            database,
        )

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
            alb_origin = origins.LoadBalancerV2Origin(
                api_service.load_balancer,
                protocol_policy=cloudfront.OriginProtocolPolicy.HTTP_ONLY,
            )
            # SSE endpoint — longer origin read timeout for streaming
            self.frontend_distribution.add_behavior(
                "/api/institution/events*",
                origins.LoadBalancerV2Origin(
                    api_service.load_balancer,
                    protocol_policy=cloudfront.OriginProtocolPolicy.HTTP_ONLY,
                    read_timeout=Duration.seconds(60),
                ),
                allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER,
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
            )
            # All other API requests
            self.frontend_distribution.add_behavior(
                "/api/*",
                alb_origin,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER,
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
            )

        api_scaling = api_service.service.auto_scale_task_count(
            min_capacity=config.api.min_count,
            max_capacity=config.api.max_count,
        )
        api_scaling.scale_on_cpu_utilization("ApiCpuScaling", target_utilization_percent=65)
        api_scaling.scale_on_memory_utilization("ApiMemoryScaling", target_utilization_percent=75)

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
        )
        worker_scaling = worker_service.auto_scale_task_count(
            min_capacity=config.worker.min_count,
            max_capacity=config.worker.max_count,
        )
        worker_scaling.scale_on_cpu_utilization("WorkerCpuScaling", target_utilization_percent=65)
        worker_scaling.scale_on_memory_utilization("WorkerMemoryScaling", target_utilization_percent=75)

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
            environment=runtime_environment,
            secrets=runtime_secrets,
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="migrations",
                log_group=migration_log_group,
            ),
        )

        recordings_bucket.grant_read_write(api_task_definition.task_role)
        recordings_bucket.grant_read_write(worker_task_definition.task_role)
        recordings_bucket.grant_read_write(migration_task_definition.task_role)

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
        CfnOutput(
            self,
            "PrivateSubnetIds",
            value=",".join(subnet.subnet_id for subnet in vpc.private_subnets),
        )
        CfnOutput(self, "MigrationTaskDefinitionArn", value=migration_task_definition.task_definition_arn)
        CfnOutput(self, "RecordingsBucketName", value=recordings_bucket.bucket_name)
        CfnOutput(self, "RedisUrl", value=redis_url)
        CfnOutput(self, "DatabaseEndpointAddress", value=database.db_instance_endpoint_address)
        CfnOutput(self, "DatabasePort", value=database.db_instance_endpoint_port)
        CfnOutput(
            self,
            "DatabaseConnectionTemplate",
            value=(
                "postgresql+asyncpg://<DATABASE_USER>:<DATABASE_PASSWORD>@"
                f"{database.db_instance_endpoint_address}:{database.db_instance_endpoint_port}/{config.database.name}"
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
        database: rds.DatabaseInstance,
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
            "DATABASE_HOST": database.db_instance_endpoint_address,
            "DATABASE_PORT": database.db_instance_endpoint_port,
            "DATABASE_NAME": self.config.database.name,
        }
        if cors_origins:
            environment["CORS_ALLOWED_ORIGINS"] = ",".join(cors_origins)
        if frontend_base_url:
            environment["AUTH_FRONTEND_BASE_URL"] = frontend_base_url
        if self.config.trusted_proxy_cidrs:
            environment["TRUSTED_PROXY_CIDRS"] = ",".join(self.config.trusted_proxy_cidrs)
        return environment

    def _build_runtime_secrets(
        self,
        jwt_secret: secretsmanager.Secret,
        encryption_key_secret: secretsmanager.Secret,
        database: rds.DatabaseInstance,
    ) -> dict[str, ecs.Secret]:
        secrets: dict[str, ecs.Secret] = {
            "JWT_SECRET": ecs.Secret.from_secrets_manager(jwt_secret),
            "ENCRYPTION_KEY": ecs.Secret.from_secrets_manager(encryption_key_secret),
        }

        if database.secret is not None:
            secrets["DATABASE_USER"] = ecs.Secret.from_secrets_manager(database.secret, "username")
            secrets["DATABASE_PASSWORD"] = ecs.Secret.from_secrets_manager(database.secret, "password")

        for env_name, secret_arn in self.config.external_secrets.items():
            secret = secretsmanager.Secret.from_secret_complete_arn(
                self,
                f"{env_name.title().replace('_', '')}ImportedSecret",
                secret_arn,
            )
            secrets[env_name] = ecs.Secret.from_secrets_manager(secret)

        return secrets

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
                {
                    "name": "AWSManagedRulesCommonRuleSet",
                    "priority": 0,
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
                    "priority": 1,
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
                    "priority": 2,
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
