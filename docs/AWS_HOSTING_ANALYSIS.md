# AWS Hosting Analysis for SolaraAlpha

**Date**: 2025-10-24
**Status**: Phase 1 (Development/Prototype)
**Objective**: Deploy orchestrator on AWS using maximum free tier benefits

---

## Executive Summary

SolaraAlpha is well-suited for AWS Free Tier hosting. The orchestrator is a lightweight, stateless daemon requiring minimal resources. **Total monthly cost: $0** for first 12 months if staying within limits.

**Key Finding**: Application requires ~720 hours/month compute (24/7 operation) - fits within 750-hour EC2 free tier.

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
│  │ EC2 Instance (t3.micro)                     │            │
│  │ ─────────────────────────────────────────   │            │
│  │ • Python 3.11 runtime                       │            │
│  │ • Conda environment                         │            │
│  │ • Claude Code CLI                           │            │
│  │ • orchestrator.py (systemd service)         │            │
│  │                                              │            │
│  │ Resources:                                   │            │
│  │   - 1 vCPU, 1GB RAM                         │            │
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

### Scenario 1: Within Free Tier (First 12 Months)

| Service | Usage | Free Tier Limit | Cost |
|---------|-------|-----------------|------|
| EC2 (t3.micro) | 720 hrs/month | 750 hrs/month | **$0** |
| EBS (gp3) | 10GB | 30GB | **$0** |
| S3 Storage | 2GB | 5GB | **$0** |
| S3 Requests | 500 PUT, 5K GET/month | 2K PUT, 20K GET | **$0** |
| CloudWatch Logs | 3GB/month | 5GB ingestion, 5GB storage | **$0** |
| CloudWatch Alarms | 5 alarms | 10 alarms | **$0** |
| SNS Notifications | 50 emails/month | 1,000 emails/month | **$0** |
| Data Transfer Out | 10GB/month | 100GB/month | **$0** |
| **TOTAL** | | | **$0.00/month** |

**Duration**: 12 months for most services

### Scenario 2: After Free Tier Expires (Month 13+)

| Service | Usage | Monthly Cost (us-east-1) |
|---------|-------|--------------------------|
| EC2 (t3.micro) | 720 hrs/month | $7.50 |
| EBS (gp3) | 10GB | $0.80 |
| S3 Storage | 2GB | $0.05 |
| S3 Requests | 500 PUT, 5K GET/month | $0.03 |
| CloudWatch Logs | 3GB/month | $1.51 (ingestion) + $0.09 (storage) |
| SNS Notifications | 50 emails/month | $0.00 (always free) |
| Data Transfer Out | 10GB/month | $0.90 |
| **TOTAL** | | **~$10.88/month** |

**Cost Optimization After Free Tier**:
- Consider Spot Instances: ~70% discount (EC2 → $2.25/month)
- Use Lambda instead of EC2 if workload can be scheduled (always free tier)
- Archive old logs to S3 Glacier Deep Archive ($0.00099/GB/month)

---

## Implementation Phases

### Phase 1: Basic Deployment (Week 1)

**Objective**: Get orchestrator running on EC2

1. **EC2 Setup**
   ```bash
   # Launch t3.micro instance (Amazon Linux 2023)
   # User data script to install dependencies
   #!/bin/bash
   yum update -y
   yum install -y git python3.11 python3.11-pip

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

### Alternative: Serverless Architecture (Always Free)

If you want to stay in free tier indefinitely, consider:

```
┌────────────────────────────────────────────────┐
│ EventBridge Rule (1M invocations free)         │
│ • Trigger: rate(5 minutes)                     │
│ • Target: Lambda function                      │
└────────────────┬───────────────────────────────┘
                 │
                 ▼
┌────────────────────────────────────────────────┐
│ Lambda Function (1M invocations/month free)    │
│ • orchestrator logic                           │
│ • fetches GitHub issues                        │
│ • spawns Claude Code workers (via API)         │
│ • updates GitHub labels                        │
└────────────────────────────────────────────────┘
```

**Pros**:
- Always free (1M invocations = 2,880 per day = every 30 seconds)
- No EC2 instance to manage
- Automatic scaling and high availability

**Cons**:
- 15-minute Lambda timeout (need to handle long-running tasks differently)
- Cold start latency (~1-2 seconds)
- More complex state management
- Claude Code CLI may not work in Lambda (investigate EFS or containerized approach)

---

## Recommendations

### For Phase 1 (Current - Development)

✅ **Deploy on AWS Free Tier immediately**

**Why**:
- Zero cost for 12 months
- Better uptime than local machine
- Practice for production deployment
- CloudWatch monitoring adds visibility
- Stateless design = easy to restart anytime

**Quick Start**:
1. Launch t3.micro EC2 instance (Amazon Linux 2023)
2. Clone repo, install dependencies
3. Store GITHUB_TOKEN in Parameter Store
4. Create systemd service
5. Configure CloudWatch Logs
6. Set up billing alerts

**Time Investment**: ~4 hours for initial setup

### For Phase 2 (Paper Trading)

✅ **Continue on AWS Free Tier with enhanced monitoring**

**Additions**:
- Separate staging instance (still within 750 hr limit if single instance)
- Enhanced CloudWatch alarms for paper trading P&L
- S3 storage for simulated trade history
- SNS alerts for large simulated losses

### For Phase 3 (Live Trading)

⚠️ **Critical Blockers Must Be Resolved First**:
1. **Data Migration**: Yahoo Finance → Twelve Data (highest priority)
2. **Risk Management**: Implement position limits, stop-loss, circuit breakers
3. **Regulatory Compliance**: Ensure legal to operate as proprietary trading firm
4. **Capital Approval**: Shareholder authorization for real money deployment

✅ **Upgrade to Paid Tier** (~$11/month is negligible for live trading)

**Additions**:
- Multi-AZ deployment for high availability
- RDS for trade history persistence (db.t3.micro free tier)
- Enhanced monitoring (CloudWatch Contributor Insights)
- Automated backups (AWS Backup)
- Consider professional-grade infrastructure once profitable

---

## Conclusion

**TL;DR**: SolaraAlpha is perfectly suited for AWS Free Tier hosting. Deploy now for zero cost, 24/7 uptime, and professional monitoring. After 12 months, costs are ~$11/month - trivial compared to trading operations.

**Next Steps**:
1. ✅ Review this document with shareholder
2. ✅ Approve AWS account creation (if needed)
3. ✅ Execute Phase 1 deployment (4 hours)
4. ✅ Monitor for 1 week, validate stability
5. ⏳ Plan Phase 2 (paper trading) in parallel with strategy development
6. ❌ Phase 3 (live trading) blocked on data migration + risk management

**Risk Assessment**: Low risk, high value. AWS Free Tier provides professional-grade infrastructure at zero cost for 12 months, with minimal ongoing costs thereafter.
