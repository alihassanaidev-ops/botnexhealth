# NexHealth: Deployment & HIPAA Compliance Runbook

Welcome! This document is designed to guide junior developers and DevOps engineers through the process of deploying the NexHealth application to AWS, transitioning from Staging to Production, and ensuring our infrastructure meets strict HIPAA compliance standards.

---

## 1. Prerequisites

Before you can deploy, ensure you have your local environment configured:
- **AWS CLI**: Installed and configured with the correct IAM user. You should have an AWS profile named `deployer` that has administrative access to manage CloudFormation, ECS, RDS, etc.
- **Node.js & npm**: Required to build the frontend dashboard (`nexus-dashboard-web`).
- **Python 3.11+**: Required for the AWS CDK infrastructure code.
- **AWS CDK CLI**: Installed globally (`npm install -g aws-cdk`).
- **Docker**: Running locally if you are testing container builds.

---

## 2. Deploying to Staging

The deployment process is broken down into three main phases: Infrastructure, Database Migration, and Frontend Publishing. We use a `Makefile` to simplify these commands.

### Step 2.1: Deploy the Infrastructure
This provisions our VPC, RDS PostgreSQL, ElastiCache Redis, ECS Fargate clusters, S3 buckets, and CloudFront distributions.

1. Ensure your AWS profile is set: `export AWS_PROFILE=deployer`
2. Navigate to the root directory and deploy:
   ```bash
   make cdk-deploy-staging
   ```
   *Note: This utilizes `infra/config/staging.json`. Ensure all Secrets Manager ARNs in this config are up to date.*

### Step 2.2: Run Database Migrations
Once the database is up, we need to apply our Alembic schema migrations via a one-off ECS task.

```bash
make cdk-run-migrations-staging
```
*This script fetches the network config from the CDK outputs, runs the Fargate migration task, and waits for it to succeed.*

### Step 2.3: Publish the Frontend
This builds the Vite React app and syncs it to the newly created S3 bucket, then invalidates the CloudFront cache.

```bash
make cdk-publish-frontend-staging
```

> **💡 Gotcha - AWS Secrets Manager:**
> If you ever add new external secrets (e.g., Twilio API Keys), **always use the full Secret ARN** in the `staging.json` or `production.json` config, NOT just the secret name. The AWS CDK method `from_secret_name_v2` sometimes fails to attach the correct IAM read permissions. We use `from_secret_complete_arn` to guarantee ECS tasks can read the secrets on startup.

---

## 3. Production Setup Overview

Deploying to production is identical to staging, but it requires a dedicated configuration file and increased resource sizing to ensure high availability.

### Creating the Production Config
1. Duplicate `infra/config/production.example.json` to `infra/config/production.json`.
2. **Scale Up Resources**:
   - `database.instanceType`: Upgrade from `t3.micro` to `m5.large` or similar.
   - `database.multiAz`: Set to `true` (Crucial for failover/redundancy).
   - `api.minCount` & `api.maxCount`: Set a minimum of `2` to ensure tasks run across multiple Availability Zones.
   - `redis.multiAzEnabled`: Set to `true`.
3. **Run Production Deploy**:
   You will need to run the CDK commands directly targeting the production config:
   ```bash
   cd infra
   AWS_PROFILE=deployer cdk deploy -c config=config/production.json
   ```

---

## 4. HIPAA Compliance & Security Architecture

Because NexHealth processes ePHI (Electronic Protected Health Information), our infrastructure must adhere to HIPAA regulations. Here is how our architecture handles compliance, and what you must enforce in Production:

### 4.1 Data in Transit (Encryption & HTTPS)
HIPAA requires that all ePHI transmitted over a network be encrypted.
- **Frontend**: Served via AWS CloudFront which enforces **HTTPS (TLS 1.2+)** by default.
- **Backend API (ACTION REQUIRED FOR PROD)**:
  - *Current Staging State*: The Application Load Balancer (ALB) outputs an `http://` address.
  - *Production Requirement*: You **must** create an ACM (AWS Certificate Manager) Certificate for your custom domain (e.g., `api.nexhealth.com`) and attach it to the ALB. Update the CDK stack to add an `HTTPS` listener to the `ecs_patterns.ApplicationLoadBalancedFargateService`. Do not expose HTTP in production.
- **Internal Traffic**: Connections to the RDS Database and Redis ElastiCache are encrypted. Redis uses `rediss://` (TLS enabled). RDS enforces SSL connections.

### 4.2 Data at Rest (Encryption)
HIPAA requires that stored ePHI cannot be read if physical access to the drives is compromised.
- **Database (RDS)**: `storage_encrypted=True` is enabled in the CDK stack. AWS manages the underlying KMS keys.
- **Cache (Redis)**: `at_rest_encryption_enabled=True` is enabled.
- **Files/Recordings (S3)**: The recordings bucket uses `s3.BucketEncryption.S3_MANAGED`. Ensure that `block_public_access=BLOCK_ALL` remains enabled so no voice recordings are accidentally exposed to the internet.

### 4.3 Network Isolation & Access Control
- **Private Subnets**: The ECS Tasks, RDS Database, and Redis Cluster are deployed into **Private Subnets**. They cannot be reached directly from the internet. They can only be accessed via the public-facing Load Balancer or through a secure VPN/Bastion host.
- **IAM Roles**: The ECS tasks run using least-privilege IAM Roles (`task_role`). They are only granted access to the specific S3 buckets and specific Secrets Manager ARNs they explicitly need.

### 4.4 Audit Controls & Logging
- **Database Auditing**: Application-level schema includes `audit_logs` to track who viewed/modified patient records.
- **AWS CloudTrail**: Ensure CloudTrail is enabled on the AWS Account to log all infrastructure API calls (who deployed what, who accessed which secret).
- **Application Logs**: API and Worker logs are securely shipped to CloudWatch Logs. **Rule for developers:** Never `print()` or log sensitive patient data, PII, or ePHI to standard output.

---

## 5. Quick Troubleshooting

- **ECS Task Fails to Start (Pending -> Stopped):**
  99% of the time, this is an IAM permission issue pulling the Docker image or reading an AWS Secret. Check the `StoppedReason` in the ECS Console. Verify the secret ARNs in `staging.json` / `production.json`.
- **Database Connection Timeouts:**
  Ensure you are not trying to connect to the RDS instance from your local machine. It is in a private subnet. To access it locally, you must set up an AWS Systems Manager (SSM) Session Manager port-forwarding tunnel.
- **Frontend Changes Not Showing:**
  CloudFront caches aggressively. Ensure `make cdk-publish-frontend-staging` successfully runs the `aws cloudfront create-invalidation` command at the end of the script.
