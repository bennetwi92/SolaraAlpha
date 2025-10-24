# AWS Hosting Analysis for SolaraAlpha

**Date**: 2025-10-24
**Status**: Phase 1 (Development/Prototype)
**Objective**: Deploy orchestrator on AWS using maximum free tier benefits

---

## Executive Summary

⚠️ **CRITICAL UPDATE**: AWS Free Tier EC2 instances (t2/t3.micro with 1GB RAM) are **insufficient** for running Claude Code (requires minimum 4GB RAM).

**Viable Options**:
1. **Use $200 AWS Credits** (new accounts): Run t3.medium (4GB) for ~6 months at zero cost
2. **Paid EC2**: t3.small (2GB) @ $15/month or t3.medium (4GB) @ $30/month
3. **Serverless Lambda**: Always free tier, but requires architecture changes (15-min timeout limit)
4. **Optimize Local**: Run orchestrator locally, defer AWS deployment to Phase 3 (production)

**Recommendation**: Option 1 (AWS Credits) for new accounts, otherwise Option 4 (local deployment) for Phase 1.

---

## Critical Finding: Free Tier Limitation

### The Problem

| Requirement | AWS Free Tier | Match? |
|-------------|---------------|---------|
| **Claude Code Minimum RAM** | 4GB | ❌ |
| **AWS Free Tier EC2** | t2/t3.micro (1GB RAM) | ❌ |
| **Hours Required** | 720/month (24/7) | ✅ (750 hrs free) |

**Conclusion**: While the 750-hour allocation is sufficient, the instance size (1GB RAM) is not. Claude Code requires minimum 4GB RAM to run reliably.

### Evidence

From official Claude Code documentation:
- **Minimum System Requirements**: 4GB+ RAM
- **Recommended**: 8GB+ RAM
- **Node.js**: 18+ required

From orchestrator analysis:
- Spawns Claude Code CLI as subprocess (line 408)
- Serial processing (one worker at a time)
- Each worker inherits full Claude Code memory requirements
- 20-minute timeout per task (WORKER_TIMEOUT = 1200s)

**Real-world observation**: Current Claude Code session uses ~297MB base + context/operations memory. Under load, this can exceed 1GB easily, causing OOM errors on t3.micro.

---

## AWS Free Tier 2025 Overview

### Major Changes (Effective July 15, 2025)
- **New Model**: 6-month credit trial ($100 base + $100 onboarding tasks = $200 total)
- **12-Month Free Tier**: Still available for EC2, RDS, S3 (traditional services)
- **Always Free**: Lambda, CloudWatch Logs (limited), DynamoDB (limited)

### Recommended Services for SolaraAlpha

| Service | Free Tier Limit | Duration | Use Case |
|---------|----------------|----------|----------|
| **EC2** | 750 hrs/month (t2.micro or t3.micro) | 12 months | Orchestrator daemon |
| **S3** | 5GB storage, 20K GET, 2K PUT/month | 12 months | Backtest results, logs |
| **CloudWatch Logs** | 5GB ingestion, 5GB storage | Always Free | Application logging |
| **Systems Manager** | Parameter Store (10K free params) | Always Free | Secrets management |
| **SNS** | 1,000 email notifications/month | Always Free | Error alerts |
| **Lambda** | 1M invocations, 400K GB-sec/month | Always Free | Optional: scheduled tasks |
| **CloudWatch Alarms** | 10 alarms | Always Free | Monitoring |
| **Data Transfer** | 100GB outbound/month | 12 months | GitHub API, market data |

---

## Recommended Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ AWS Account (Free Tier)                                      │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌─────────────────────────────────────────────┐            │
│  │ EC2 Instance (t3.medium)                    │            │
│  │ ─────────────────────────────────────────   │            │
│  │ • Python 3.11 runtime                       │            │
│  │ • Node.js 18+ (for Claude Code)             │            │
│  │ • Conda environment                         │            │
│  │ • Claude Code CLI                           │            │
│  │ • orchestrator.py (systemd service)         │            │
│  │                                              │            │
│  │ Resources:                                   │            │
│  │   - 2 vCPU, 4GB RAM (Claude Code minimum)   │            │
│  │   - 10GB EBS (gp3)                          │            │
│  │   - Amazon Linux 2023 or Ubuntu 22.04       │            │
│  └──────────────┬──────────────────────────────┘            │
│                 │                                             │
│                 │ Logs                                        │
│                 ▼                                             │
│  ┌──────────────────────────────────┐                       │
│  │ CloudWatch Logs                   │                       │
│  │ • /aws/ec2/orchestrator/stdout    │                       │
│  │ • /aws/ec2/orchestrator/stderr    │                       │
│  │ • 5GB retention (free tier)       │                       │
│  └──────────────┬───────────────────┘                       │
│                 │                                             │
│                 │ Metrics                                     │
│                 ▼                                             │
│  ┌──────────────────────────────────┐                       │
│  │ CloudWatch Alarms (10 free)       │                       │
│  │ • High error rate                 │                       │
│  │ • Orchestrator down               │                       │
│  │ • Timeout rate threshold          │                       │
│  └──────────────┬───────────────────┘                       │
│                 │                                             │
│                 │ Alerts                                      │
│                 ▼                                             │
│  ┌──────────────────────────────────┐                       │
│  │ SNS Topic (1K emails/month free)  │                       │
│  │ • Email to shareholder            │                       │
│  └───────────────────────────────────┘                       │
│                                                               │
│  ┌─────────────────────────────────────────────┐            │
│  │ Systems Manager Parameter Store              │            │
│  │ • /solara/GITHUB_TOKEN (SecureString)        │            │
│  │ • /solara/TWELVE_DATA_API_KEY (Phase 3)      │            │
│  │ • /solara/SOLARA_ENV (String)                │            │
│  └──────────────────────────────────────────────┘            │
│                                                               │
│  ┌─────────────────────────────────────────────┐            │
│  │ S3 Bucket (5GB free)                         │            │
│  │ • s3://solara-alpha-backtest-results/        │            │
│  │ • s3://solara-alpha-logs-archive/            │            │
│  └──────────────────────────────────────────────┘            │
│                                                               │
│  ┌─────────────────────────────────────────────┐            │
│  │ IAM Role: SolaraOrchestratorRole             │            │
│  │ Policies:                                     │            │
│  │   - SSM GetParameter (read secrets)          │            │
│  │   - S3 PutObject (write results)             │            │
│  │   - CloudWatch PutLogEvents (write logs)     │            │
│  │   - SNS Publish (send alerts)                │            │
│  └──────────────────────────────────────────────┘            │
│                                                               │
└───────────────────────────────────────────────────────────────┘

External Dependencies (not in AWS):
  • GitHub API (issues, labels, comments)
  • Yahoo Finance API (market data - Phase 1)
  • Twelve Data API (market data - Phase 3)
  • Anthropic Claude API (Claude Code workers)
```

---

## Cost Analysis

⚠️ **NOTE**: t3.micro (1GB RAM) is insufficient for Claude Code. Minimum viable instance is t3.small (2GB RAM), though t3.medium (4GB RAM) is recommended.

### Scenario 1: New AWS Account with $200 Credits (Months 1-6)

**New accounts get up to $200 in credits** ($100 base + $100 from onboarding tasks). This covers a t3.medium for ~6 months.

| Service | Usage | Monthly Cost | Credit Coverage |
|---------|-------|--------------|-----------------|
| EC2 (t3.medium) | 720 hrs/month | $30.24 | ✅ ~6.6 months |
| EBS (gp3) | 10GB | $0.80 | ✅ Negligible |
| S3 Storage | 2GB | $0.00 | Free tier |
| CloudWatch Logs | 3GB/month | $0.00 | Always free |
| SNS Notifications | 50 emails/month | $0.00 | Always free |
| Data Transfer Out | 10GB/month | $0.00 | Free tier (12 months) |
| **TOTAL** | | **~$31/month** | **~6 months at $0 cost** |

**After credits expire** (Month 7): Pay $31/month or migrate to alternative architecture.

### Scenario 2: Standard Free Tier (INSUFFICIENT - DO NOT USE)

| Service | Usage | Free Tier Limit | Cost | Viable? |
|---------|-------|-----------------|------|---------|
| EC2 (t3.micro) | 720 hrs/month | 750 hrs/month | $0 | ❌ Only 1GB RAM |

**Conclusion**: While technically free, **t3.micro cannot run Claude Code reliably**. Expect OOM errors and system instability.

### Scenario 3: Paid EC2 (Minimal Viable - t3.small)

| Service | Usage | Monthly Cost (us-east-1) |
|---------|-------|--------------------------|
| EC2 (t3.small) | 720 hrs/month | $15.12 |
| EBS (gp3) | 10GB | $0.80 |
| S3 Storage | 2GB | $0.00 (free tier 12 months) |
| CloudWatch Logs | 3GB/month | $0.00 (always free) |
| SNS Notifications | 50 emails/month | $0.00 (always free) |
| Data Transfer Out | 10GB/month | $0.00 (free tier 12 months) |
| **TOTAL** | | **~$15.92/month** |

**Notes**:
- t3.small has 2GB RAM (50% of Claude Code minimum)
- May still experience memory pressure under load
- Recommended for cost-conscious deployments only

### Scenario 4: Paid EC2 (Recommended - t3.medium)

| Service | Usage | Monthly Cost (us-east-1) |
|---------|-------|--------------------------|
| EC2 (t3.medium) | 720 hrs/month | $30.24 |
| EBS (gp3) | 10GB | $0.80 |
| S3 Storage | 2GB | $0.00 (free tier 12 months) |
| CloudWatch Logs | 3GB/month | $0.00 (always free) |
| SNS Notifications | 50 emails/month | $0.00 (always free) |
| Data Transfer Out | 10GB/month | $0.00 (free tier 12 months) |
| **TOTAL** | | **~$31.04/month** |

**Notes**:
- t3.medium has 4GB RAM (meets Claude Code minimum)
- Stable, reliable operation expected
- Best choice for production deployment

### Scenario 5: Cost Optimization (Spot Instances)

| Service | Usage | Monthly Cost (us-east-1) | Savings |
|---------|-------|--------------------------|---------|
| EC2 (t3.medium Spot) | 720 hrs/month | $9.07 (~70% off) | ~$21/month |
| EBS (gp3) | 10GB | $0.80 | - |
| Other services | Same as above | $0.00 | - |
| **TOTAL** | | **~$9.87/month** | **~$21/month** |

**Tradeoffs**:
- Can be interrupted with 2-minute warning
- Orchestrator is stateless, restarts gracefully
- Must handle interruptions (checkpoint work, save state to GitHub)
- 90%+ availability expected in most regions

### Cost Comparison: All Scenarios

| Scenario | Monthly Cost | Duration | Total 12-Month Cost |
|----------|--------------|----------|---------------------|
| **1. AWS Credits (New Account)** | $0 (months 1-6), $31 (months 7-12) | 12 months | **$186** |
| **2. Free Tier (t3.micro)** | $0 | 12 months | **$0** (but ❌ insufficient) |
| **3. Paid (t3.small)** | $15.92 | 12 months | **$191** |
| **4. Paid (t3.medium)** | $31.04 | 12 months | **$372** |
| **5. Spot (t3.medium)** | $9.87 | 12 months | **$118** |

**Winner**: Scenario 1 (AWS Credits for new accounts) or Scenario 5 (Spot for existing accounts)

---

## Implementation Phases

### Phase 1: Basic Deployment (Week 1)

**Objective**: Get orchestrator running on EC2

1. **EC2 Setup**
   ```bash
   # Launch t3.medium instance (Amazon Linux 2023)
   # ⚠️ NOTE: t3.micro (1GB RAM) is insufficient for Claude Code
   # User data script to install dependencies
   #!/bin/bash
   yum update -y
   yum install -y git python3.11 python3.11-pip

   # Install Node.js 18+ (required for Claude Code)
   curl -fsSL https://rpm.nodesource.com/setup_18.x | bash -
   yum install -y nodejs

   # Install Claude Code CLI
   npm install -g @anthropic-ai/claude-code

   # Install Miniconda
   wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
   bash Miniconda3-latest-Linux-x86_64.sh -b -p /opt/miniconda

   # Clone repository
   git clone https://github.com/bennetwi92/SolaraAlpha.git /opt/solara
   cd /opt/solara

   # Setup environment
   /opt/miniconda/bin/conda env create -f environment.yml
   /opt/miniconda/bin/conda activate solaraalpha
   pip install -r requirements.txt

   # Configure Claude Code (requires manual authentication)
   # Run: claude --login
   ```

2. **Secrets Management**
   ```bash
   # Store GitHub token in Parameter Store (run from local machine)
   aws ssm put-parameter \
     --name /solara/GITHUB_TOKEN \
     --value "ghp_xxxxxxxxxxxx" \
     --type SecureString \
     --description "GitHub API token for orchestrator"

   # Store environment
   aws ssm put-parameter \
     --name /solara/SOLARA_ENV \
     --value "development" \
     --type String
   ```

3. **IAM Role Configuration**
   - Create role: `SolaraOrchestratorRole`
   - Attach policies:
     - `AmazonSSMReadOnlyAccess` (built-in)
     - Custom inline policy for S3, CloudWatch, SNS

4. **Systemd Service**
   ```ini
   # /etc/systemd/system/solara-orchestrator.service
   [Unit]
   Description=SolaraAlpha Orchestrator
   After=network.target

   [Service]
   Type=simple
   User=ec2-user
   WorkingDirectory=/opt/solara
   Environment="PATH=/opt/miniconda/envs/solaraalpha/bin:/usr/bin"
   ExecStartPre=/bin/bash -c 'source /opt/miniconda/etc/profile.d/conda.sh && conda activate solaraalpha'
   ExecStart=/opt/miniconda/envs/solaraalpha/bin/python src/orchestrator/orchestrator.py
   Restart=always
   RestartSec=10
   StandardOutput=journal
   StandardError=journal

   [Install]
   WantedBy=multi-user.target
   ```

5. **CloudWatch Logs Agent**
   ```bash
   # Install CloudWatch agent
   wget https://s3.amazonaws.com/amazoncloudwatch-agent/amazon_linux/amd64/latest/amazon-cloudwatch-agent.rpm
   rpm -U ./amazon-cloudwatch-agent.rpm

   # Configure to stream journald logs
   # Config file: /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json
   ```

### Phase 2: Monitoring & Alerts (Week 2)

1. **CloudWatch Alarms**
   - Orchestrator process down (heartbeat missing)
   - High error rate (>5 errors in 5 minutes)
   - High timeout rate (>50% tasks timeout)

2. **SNS Topic & Email Subscription**
   ```bash
   aws sns create-topic --name solara-alerts
   aws sns subscribe \
     --topic-arn arn:aws:sns:us-east-1:123456789012:solara-alerts \
     --protocol email \
     --notification-endpoint shareholder@example.com
   ```

3. **Custom Metrics**
   - Modify orchestrator to publish metrics:
     - Tasks completed per hour
     - Tasks blocked per hour
     - Worker spawn failures
     - GitHub API rate limit remaining

### Phase 3: Storage & Backups (Week 3)

1. **S3 Bucket Setup**
   ```bash
   # Create bucket for backtest results
   aws s3 mb s3://solara-alpha-backtest-results

   # Enable versioning
   aws s3api put-bucket-versioning \
     --bucket solara-alpha-backtest-results \
     --versioning-configuration Status=Enabled

   # Set lifecycle policy (archive to Glacier after 90 days)
   aws s3api put-bucket-lifecycle-configuration \
     --bucket solara-alpha-backtest-results \
     --lifecycle-configuration file://lifecycle-policy.json
   ```

2. **Automated Backups**
   - EC2 snapshots: AWS Backup (free for first 90 days of storage)
   - Git repository: already on GitHub
   - No database to backup (GitHub is source of truth)

### Phase 4: Cost Monitoring (Ongoing)

1. **AWS Budgets**
   ```bash
   # Set budget alert at $1/month (should never hit if staying in free tier)
   aws budgets create-budget \
     --account-id 123456789012 \
     --budget file://budget.json \
     --notifications-with-subscribers file://notifications.json
   ```

2. **Free Tier Usage Alerts**
   - Enable in AWS Billing Console
   - Alert at 85% of free tier usage
   - Alert when exceeding free tier

---

## Security Considerations

### Network Security

1. **Security Group Configuration**
   ```
   Inbound Rules:
     - SSH (22): 0.0.0.0/0 (for initial setup only - restrict to your IP)

   Outbound Rules:
     - HTTPS (443): 0.0.0.0/0 (GitHub API, market data APIs, Anthropic API)
     - HTTP (80): 0.0.0.0/0 (redirect to HTTPS)
   ```

2. **No Inbound Traffic Required**
   - Orchestrator is fully outbound (fetches from GitHub)
   - No web server, no API endpoints
   - SSH only for maintenance (consider AWS Systems Manager Session Manager instead)

### Secrets Management Best Practices

1. **Never hardcode credentials**
   - Use Systems Manager Parameter Store (SecureString)
   - Retrieve at runtime via IAM role

2. **Rotate secrets regularly**
   - GitHub tokens: rotate every 90 days
   - Twelve Data API key: rotate before Phase 3 deployment

3. **Principle of Least Privilege**
   - IAM role only has permissions needed (SSM read, S3 write, CloudWatch write)
   - No EC2 instance profile permissions for terminate, stop, modify

### Data Security

1. **S3 Bucket Encryption**
   - Enable default encryption (AES-256)
   - No public access (private bucket only)

2. **CloudWatch Logs**
   - Ensure no secrets logged (scrub GITHUB_TOKEN from output)
   - Consider log encryption at rest

---

## Operational Runbook

### Daily Operations

**No daily intervention required** - orchestrator is autonomous.

### Weekly Reviews

1. Check CloudWatch dashboard for anomalies
2. Review GitHub issues marked as `status:blocked`
3. Verify free tier usage in AWS Billing Console

### Monthly Tasks

1. Review CloudWatch logs for errors
2. Verify S3 storage usage (<5GB)
3. Check AWS Free Tier usage alerts
4. Review GitHub API rate limit usage

### Incident Response

**Orchestrator Down**:
1. Check CloudWatch Logs for errors
2. SSH to EC2 instance: `sudo systemctl status solara-orchestrator`
3. Restart service: `sudo systemctl restart solara-orchestrator`
4. If persistent failure: check GitHub token expiry, API rate limits

**High Error Rate**:
1. Review CloudWatch Logs for specific error messages
2. Check GitHub issues for pattern (all same component?)
3. If Claude Code worker failures: verify Anthropic API status
4. If GitHub API errors: check rate limit status

**Approaching Free Tier Limits**:
1. EC2 hours >700/month: optimize restart frequency, consider scheduled operation
2. CloudWatch Logs >4GB: reduce log verbosity, archive old logs
3. S3 storage >4GB: implement lifecycle policy, move to Glacier
4. Data transfer >90GB: investigate excessive API calls, optimize polling frequency

---

## Migration Path from Development to Production

### Current Status: Phase 1 (Development)

- **Data Source**: Yahoo Finance
- **Environment**: `SOLARA_ENV=development`
- **Capital**: $0 (no real trading)
- **Deployment**: Local machine or AWS (development instance)

### Phase 2: Paper Trading (~2-3 months)

- **Data Source**: Still Yahoo Finance (acceptable for simulation)
- **Environment**: `SOLARA_ENV=staging`
- **Capital**: $0 (simulated orders)
- **Deployment**: AWS (dedicated staging instance)
- **Changes Required**:
  - Implement order simulation logic
  - Add paper trading P&L tracking
  - Enhanced monitoring and alerting

### Phase 3: Live Trading (Requires Data Migration)

- **Data Source**: **MUST MIGRATE TO TWELVE DATA** (Yahoo Finance violates ToS for commercial use)
- **Environment**: `SOLARA_ENV=production`
- **Capital**: Real capital (start small, scale gradually)
- **Deployment**: AWS (production instance with high availability)
- **Blockers**:
  - ❌ Yahoo Finance data migration to Twelve Data (800 calls/day free tier)
  - ❌ Regulatory compliance review
  - ❌ Risk management system validation
  - ❌ Shareholder approval for capital deployment

**Critical**: Runtime safeguard enforces this: `SOLARA_ENV=production` + Yahoo Finance = ValueError

---

## Free Tier Sustainability Analysis

### Services That Stay Free Forever

| Service | Always Free Limit | SolaraAlpha Usage | Sustainable? |
|---------|-------------------|-------------------|--------------|
| Lambda | 1M invocations/month | Not used (EC2 model) | N/A |
| CloudWatch Logs | 5GB ingestion/month | ~3GB/month | ✅ Yes |
| SNS | 1,000 emails/month | ~50/month | ✅ Yes |
| Parameter Store | 10,000 parameters | 3 parameters | ✅ Yes |

### Services That Expire After 12 Months

| Service | Free for 12 Months | Post-Expiry Cost | Mitigation |
|---------|-------------------|------------------|------------|
| EC2 | 750 hrs/month | $7.50/month | Switch to Spot (~$2.25) or Lambda |
| EBS | 30GB | $0.80/month | Acceptable cost |
| S3 | 5GB | $0.05/month | Acceptable cost |
| Data Transfer | 100GB/month | $0.90/month | Acceptable cost |

**Decision Point (Month 11)**:
- If profitable: Accept ~$11/month cost (trivial vs trading profits)
- If not profitable: Migrate to Lambda-based scheduled execution
- If project paused: Snapshot instance, terminate, restart when needed

### Serverless Architectures (Detailed Analysis)

#### Option A: Pure Lambda (Limited Viability)

**Architecture**:
```
┌────────────────────────────────────────────────┐
│ EventBridge Rule (free)                        │
│ • Trigger: rate(5 minutes)                     │
│ • Target: Lambda function                      │
└────────────────┬───────────────────────────────┘
                 │
                 ▼
┌────────────────────────────────────────────────┐
│ Lambda Function (up to 10GB RAM)               │
│ • orchestrator logic                           │
│ • fetches GitHub issues                        │
│ • spawns Claude Code workers (subprocess)      │
│ • updates GitHub labels                        │
└────────────────────────────────────────────────┘
```

**Free Tier Limits (Always Free)**:
- 1M requests/month
- 400,000 GB-seconds/month compute time

**Cost Calculation**:
- Polling: 8,640 invocations/month (every 5 min) @ 128MB for 3 seconds = 3,317 GB-seconds
- Processing: Assume 10 tasks/day @ 4GB for 5 minutes each = 14,400 GB-seconds/day × 30 = 432,000 GB-seconds/month
- **Total**: 435,317 GB-seconds/month
- **Exceeds free tier by**: 35,317 GB-seconds
- **Cost**: 35,317 × $0.0000166667 = **$0.59/month** (negligible!)

**Critical Blockers**:
1. ❌ **15-minute Lambda timeout** (orchestrator tasks can take 20+ minutes)
2. ❓ **Claude Code in Lambda**: Untested - may not work in Lambda environment
3. ⚠️ **Container image required**: Need to package Claude Code CLI (Node.js + dependencies)

**Verdict**: ❌ Not viable due to timeout limitations

---

#### Option B: Lambda + Fargate Spot (BEST SERVERLESS OPTION)

**Architecture**:
```
┌─────────────────────────────────────────────────────┐
│ EventBridge Rule (free)                              │
│ • Trigger: rate(5 minutes)                          │
└─────────────────┬───────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────┐
│ Lambda Orchestrator (128MB, 5 sec runtime)           │
│ • Check GitHub for priority issues                   │
│ • If work found: Launch Fargate Spot task           │
│ • If no work: Exit (minimal cost)                    │
└─────────────────┬───────────────────────────────────┘
                  │
                  │ Spawns ECS Task
                  ▼
┌─────────────────────────────────────────────────────┐
│ ECS Fargate Spot Task (on-demand)                   │
│ • 1 vCPU, 4GB RAM                                    │
│ • Runs Claude Code worker                            │
│ • No timeout limits (can run hours if needed)        │
│ • Updates GitHub, then terminates                    │
│ • Only charged for actual runtime (per second)       │
└──────────────────────────────────────────────────────┘
```

**Cost Breakdown (us-east-1)**:

| Component | Usage | Cost/Month |
|-----------|-------|------------|
| **Lambda (orchestrator)** | 8,640 checks @ 128MB, 5 sec | $0.00 (free tier) |
| **EventBridge** | 8,640 rules/month | $0.00 (free tier) |
| **Fargate Spot (1 vCPU + 4GB)** | Assume 2 hours/day active | ~$6.00 |
| **CloudWatch Logs** | 3GB/month | $0.00 (always free) |
| **Total** | | **~$6.00/month** |

**Fargate Spot Pricing** (us-east-1):
- **CPU**: $0.04048/hour × 0.7 (Spot discount) = $0.02834/hour
- **Memory**: $0.004445/GB/hour × 0.7 (Spot discount) = $0.003112/GB/hour
- **1 vCPU + 4GB**: ($0.02834) + (4 × $0.003112) = $0.02834 + $0.01245 = **$0.04079/hour**
- **2 hours/day** × 30 days = 60 hours/month
- **Monthly cost**: 60 × $0.04079 = **$2.45/month**

Actually, let me recalculate more conservatively:
- **24/7 operation** (always running, like EC2): 720 hours/month
- **Cost**: 720 × $0.04079 = **$29.37/month** (equivalent to t3.medium!)

But the **key advantage**: Only pay for actual work time!
- If orchestrator processes 10 tasks/day @ 10 minutes each = 100 minutes/day = 1.67 hours/day
- 1.67 hours/day × 30 days = **50 hours/month**
- **Cost**: 50 × $0.04079 = **$2.04/month**

**Realistic Cost Estimate** (Phase 1 - low activity):
- **10 tasks/day** @ 10 minutes each = **$2.04/month**
- **30 tasks/day** @ 10 minutes each = **$6.12/month**
- **100 tasks/day** @ 10 minutes each = **$20.40/month**

**Benefits**:
- ✅ No timeout limits (Fargate can run for hours)
- ✅ Only pay for actual processing time (not idle time)
- ✅ Lambda orchestrator always free (within 1M requests)
- ✅ Auto-scales: Multiple Fargate tasks can run in parallel if needed
- ✅ Production-ready: Automatic retries, health checks, integration with CloudWatch

**Tradeoffs**:
- More complex setup (need Docker container image)
- Fargate Spot can be interrupted (2-minute warning)
- Slightly higher complexity vs single EC2 instance

**Verdict**: ✅ **BEST OPTION for bursty workloads** - cheaper than EC2 Spot if <50 hours/month of actual work

---

#### Option C: Step Functions + Fargate (Advanced Orchestration)

**Architecture**:
```
┌─────────────────────────────────────────────────────┐
│ EventBridge Rule (free)                              │
│ • Trigger: rate(5 minutes)                          │
└─────────────────┬───────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────┐
│ Step Functions Standard Workflow                     │
│ • Lambda: Check GitHub for priority issues           │
│ • Choice: Work found?                                │
│   - Yes: ECS RunTask.sync (launch Fargate)          │
│   - No: Exit (no cost)                               │
└─────────────────┬───────────────────────────────────┘
                  │
                  │ ecs:runTask.sync
                  ▼
┌─────────────────────────────────────────────────────┐
│ ECS Fargate Spot Task                                │
│ • Step Functions waits for completion (.sync)        │
│ • No timeout limits                                   │
│ • Automatic retry on failure                         │
└──────────────────────────────────────────────────────┘
```

**Free Tier**:
- Step Functions: 4,000 state transitions/month (always free)

**Cost Calculation**:

| Component | Usage | Cost/Month |
|-----------|-------|------------|
| **EventBridge** | 8,640 triggers | $0.00 (free) |
| **Lambda (GitHub check)** | 8,640 invocations @ 128MB, 3 sec | $0.00 (free tier) |
| **Step Functions** | 17,280 transitions (2 per workflow) | $0.33 |
| **Fargate Spot** | 50 hours/month @ 1 vCPU + 4GB | $2.04 |
| **Total** | | **$2.37/month** |

**Step Functions Cost Breakdown**:
- 8,640 workflows/month
- 2 state transitions per workflow: (1) Lambda check + (2) Fargate task
- Total: 17,280 state transitions
- Free tier: 4,000 transitions
- Paid: 13,280 transitions × $0.000025 = **$0.33/month**

**Benefits**:
- ✅ Visual workflow designer (easy to understand and debug)
- ✅ Built-in error handling and retry logic
- ✅ Native ECS integration with `.sync` (waits for completion)
- ✅ Execution history and monitoring in console
- ✅ Can add complex branching logic easily

**Tradeoffs**:
- ⚠️ Adds $0.33/month cost vs pure Lambda + Fargate
- ⚠️ More components to configure (EventBridge → Step Functions → Lambda → Fargate)
- ⚠️ Slightly more complex than direct Lambda → Fargate
- ⚠️ Only useful if you need advanced orchestration features

**Verdict**: 🟡 **Viable but overkill for simple orchestration**
- Use if you want visual workflows and built-in retry logic
- Skip if you want simplest architecture (Lambda + Fargate is simpler)
- The $0.33/month cost is negligible, but adds complexity

**When to use Step Functions**:
- Need complex multi-step workflows
- Want visual monitoring and debugging
- Require sophisticated error handling branching
- Team prefers low-code workflow design

**When to skip Step Functions**:
- Simple orchestration (just spawn worker)
- Prefer code-based logic over visual workflows
- Want fewest moving parts

---

### Serverless Cost Comparison

| Architecture | Monthly Cost | Best For | Timeout Limit | Complexity |
|--------------|--------------|----------|---------------|------------|
| **Pure Lambda** | $0.59/month | N/A | ❌ 15 minutes | Low |
| **Lambda + Fargate Spot** | $2-20/month | Bursty workloads | ✅ None | Medium |
| **Step Functions + Fargate Spot** | $2.37-20.73/month | Visual workflows, complex orchestration | ✅ None | High |
| **EC2 Spot (t3.medium)** | $9.87/month | 24/7 operation | ✅ None | Low |
| **EC2 On-Demand (t3.medium)** | $31.04/month | Stable, predictable | ✅ None | Low |

**Cost comparison at different activity levels**:
- **10 tasks/day**: Lambda + Fargate = $2.04, Step Functions + Fargate = $2.37 (+16%)
- **30 tasks/day**: Lambda + Fargate = $6.12, Step Functions + Fargate = $6.45 (+5%)
- **100 tasks/day**: Lambda + Fargate = $20.40, Step Functions + Fargate = $20.73 (+2%)

**Winner for Serverless**: Lambda + Fargate Spot ($2-6/month, simplest serverless architecture)

---

### When to Use Serverless vs EC2

**Use Lambda + Fargate Spot if**:
- ✅ Workload is bursty (<50 hours/month of actual processing)
- ✅ Want to minimize costs during low-activity periods
- ✅ Comfortable with Docker and ECS configuration
- ✅ Want automatic scaling for parallel task processing

**Use EC2 Spot if**:
- ✅ Workload is continuous (many issues to process daily)
- ✅ Want simpler deployment (single instance, systemd service)
- ✅ Processing >100 hours/month (EC2 becomes cheaper)
- ✅ Prefer traditional server management

**Breakeven Analysis**:
- **EC2 Spot**: $9.87/month (fixed, regardless of usage)
- **Fargate Spot**: $0.04079/hour
- **Breakeven**: $9.87 / $0.04079 = **242 hours/month**
- **Conclusion**: Use Fargate if processing <242 hours/month (~8 hours/day)

---

## Recommendations

### For Phase 1 (Current - Development)

⚠️ **AWS Free Tier is NOT viable** due to Claude Code's 4GB RAM requirement (free tier only offers 1GB).

#### Option A: New AWS Account (RECOMMENDED)

✅ **Use $200 AWS Credits for ~6 months at zero cost**

**Why**:
- New accounts get $200 credits ($100 base + $100 onboarding)
- Covers t3.medium (4GB RAM) for 6+ months
- Zero cost during development phase
- Professional infrastructure and monitoring
- Practice for production deployment

**Quick Start**:
1. Create new AWS account (get $200 credits)
2. Complete 5 onboarding tasks for additional $100
3. Launch t3.medium EC2 instance (Amazon Linux 2023)
4. Clone repo, install dependencies (Python 3.11, Node.js 18+, Claude Code CLI)
5. Store GITHUB_TOKEN in Parameter Store
6. Create systemd service
7. Configure CloudWatch Logs and alarms
8. Set up billing alerts

**Time Investment**: ~4-6 hours for initial setup

**After Credits Expire (Month 7)**:
- Option 1: Pay $31/month (acceptable if trading is profitable)
- Option 2: Migrate to Spot Instances (~$10/month)
- Option 3: Migrate to local deployment
- Option 4: Explore Lambda-based architecture

#### Option B: Existing AWS Account

🟡 **Pay $16-31/month or use Spot Instances (~$10/month)**

**Cost Options**:
- t3.small (2GB RAM): $16/month - minimal viable, may have memory pressure
- t3.medium (4GB RAM): $31/month - recommended for stable operation
- t3.medium Spot: $10/month - best value, handles interruptions gracefully

**Trade-offs**:
- t3.small may still experience OOM under load
- Spot instances can be interrupted (orchestrator restarts gracefully)
- $10-31/month is acceptable cost if system generates trading profits

#### Option C: Lambda + Fargate Spot Serverless (BEST VALUE)

✅ **Use serverless architecture for ultra-low cost ($2-6/month)**

**Why**:
- Only pay for actual processing time (not idle time)
- Perfect for Phase 1 low-activity workloads
- Lambda orchestrator is always free
- Auto-scales if workload increases
- No server management

**Architecture**:
- EventBridge triggers Lambda every 5 minutes
- Lambda checks GitHub for issues
- If work found: Spawns Fargate Spot task with Claude Code
- Fargate processes task, updates GitHub, terminates
- Lambda always free, Fargate billed per second

**Cost Examples** (based on activity):
- 10 tasks/day @ 10 min each: **$2.04/month**
- 30 tasks/day @ 10 min each: **$6.12/month**
- 100 tasks/day @ 10 min each: **$20.40/month**

**Setup Requirements**:
1. Create Docker image with Claude Code CLI + orchestrator
2. Push to ECR (Elastic Container Registry)
3. Create ECS Fargate Spot task definition
4. Create Lambda function to spawn Fargate tasks
5. Configure EventBridge to trigger Lambda every 5 minutes

**Time Investment**: ~6-8 hours for initial setup (more complex than EC2)

**Trade-offs**:
- More complex setup (Docker, ECS, Lambda integration)
- Requires Docker and AWS ECS knowledge
- Fargate Spot can be interrupted (handles gracefully)
- Not ideal for high-volume continuous processing (>8 hours/day)

**Recommended for Phase 1 if**:
- Want minimal AWS costs (<$10/month)
- Workload is bursty (few tasks per day)
- Comfortable with Docker and serverless architectures
- Want to experiment with modern cloud-native patterns

**Breakeven**: Use Fargate Spot if processing <8 hours/day, otherwise EC2 Spot is cheaper

#### Option D: Local Deployment (ZERO COST)

✅ **Run orchestrator on local machine, defer AWS to Phase 3**

**Why**:
- Zero cost during development
- Local machine likely has 8GB+ RAM (sufficient for Claude Code)
- Orchestrator is stateless (easy to migrate later)
- AWS deployment can wait until Phase 3 (production trading)

**Trade-offs**:
- No 24/7 uptime (requires local machine running)
- No CloudWatch monitoring (manual log review)
- Manual restart if machine reboots
- Not suitable for production, but acceptable for Phase 1 (R&D)

**Recommended for Phase 1 if**:
- Not willing to pay for AWS (even $2-6/month)
- Don't have access to new AWS account ($200 credits)
- System is not yet profitable (still in R&D)
- Want simplest possible setup

### For Phase 2 (Paper Trading)

🟡 **Continue current deployment with enhanced monitoring**

**If on AWS**:
- Keep current instance (t3.medium or t3.medium Spot)
- Add enhanced CloudWatch alarms for paper trading P&L
- Configure S3 storage for simulated trade history
- Set up SNS alerts for large simulated losses

**If on Local**:
- Consider migrating to AWS at this stage
- Paper trading benefits from 24/7 uptime
- Use Spot Instances (~$10/month) for cost efficiency

### For Phase 3 (Live Trading - PRODUCTION)

⚠️ **Critical Blockers Must Be Resolved First**:
1. **Data Migration**: Yahoo Finance → Twelve Data (HIGHEST PRIORITY)
2. **Risk Management**: Implement position limits, stop-loss, circuit breakers
3. **Regulatory Compliance**: Ensure legal to operate as proprietary trading firm
4. **Capital Approval**: Shareholder authorization for real money deployment

✅ **Deploy on AWS with production-grade infrastructure**

**Requirements**:
- t3.medium or larger (4GB+ RAM)
- Consider Spot Instances (~$10/month) if handling interruptions
- Multi-AZ deployment for high availability (optional)
- Enhanced monitoring and alerting
- Automated backups
- Professional-grade error handling

**Cost Expectations**:
- Basic (Spot): $10/month
- Standard (On-Demand): $31/month
- High Availability (Multi-AZ): $62/month

**Decision Point**: If system is profitable, $10-62/month is negligible compared to trading operations.

---

## Conclusion

**TL;DR**: AWS Free Tier alone is insufficient for SolaraAlpha due to Claude Code's 4GB RAM requirement. However, multiple viable paths exist depending on your situation.

### Decision Matrix

| Your Situation | Recommended Path | Cost (Year 1) | Best For |
|----------------|------------------|---------------|----------|
| **New to AWS** | Create account, use $200 credits | $186 | Zero upfront cost, professional deployment |
| **Existing AWS, bursty workload** | Lambda + Fargate Spot | $24-72 | Ultra-low cost, pay-per-use |
| **Existing AWS, high activity** | t3.medium Spot | $118 | Best value for 24/7 operation |
| **Existing AWS, need stability** | t3.medium On-Demand | $372 | Predictable costs, no interruptions |
| **Early-stage R&D, no budget** | Local deployment | $0 | Zero cost, defer AWS until profitable |

**Cost Breakdown by Activity Level**:
- **Low activity** (10-30 tasks/day): Lambda + Fargate Spot = $2-6/month ✅ BEST
- **Medium activity** (100 tasks/day): Lambda + Fargate Spot = $20/month ✅ BEST
- **High activity** (>242 hrs/month): EC2 Spot = $10/month ✅ BEST
- **24/7 continuous**: EC2 Spot = $10/month ✅ BEST

### Next Steps

**If pursuing AWS deployment**:
1. ✅ Review this document with shareholder
2. ✅ Decide on deployment path (credits, paid, or local)
3. ✅ Create AWS account (if new) and claim $200 credits
4. ✅ Execute deployment (4-6 hours)
5. ✅ Monitor for 1 week, validate stability
6. ⏳ Plan Phase 2 (paper trading) in parallel with strategy development
7. ❌ Phase 3 (live trading) blocked on data migration + risk management

**If deferring AWS deployment**:
1. ✅ Continue running orchestrator locally
2. ✅ Focus on strategy development and backtesting
3. ⏳ Revisit AWS deployment at Phase 2 (paper trading) or Phase 3 (production)

### Risk Assessment

**AWS Deployment**:
- **Risk**: Low to Medium (depends on cost tolerance)
- **Value**: High (professional monitoring, 24/7 uptime, practice for production)
- **Blocker**: Cost ($0-31/month depending on approach)

**Local Deployment**:
- **Risk**: Low (zero financial risk)
- **Value**: Medium (no monitoring, manual operation, not production-ready)
- **Blocker**: None (can deploy immediately)

**Recommendation**:
- **New AWS users**: Use $200 credits for 6 months free deployment (t3.medium EC2)
- **Existing AWS users with bursty workload**: Lambda + Fargate Spot (~$2-6/month) for ultra-low cost
- **Existing AWS users with high activity**: EC2 Spot (~$10/month) for 24/7 operation
- **Zero budget**: Run locally during Phase 1, migrate to AWS for Phase 3 when profitable

**Serverless is the winner** for most Phase 1 scenarios due to pay-per-use pricing!
