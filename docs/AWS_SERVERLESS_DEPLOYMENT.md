# AWS Serverless Deployment Guide - Lambda + Fargate Spot

**Architecture**: EventBridge → Lambda → Fargate Spot
**Cost**: $2-6/month for Phase 1 (10-30 tasks/day)
**Best For**: Bursty workloads, pay-per-use pricing

---

## Overview

This guide documents how to deploy the SolaraAlpha orchestrator using AWS serverless services:
- **EventBridge**: Triggers Lambda every 5 minutes (free)
- **Lambda**: Checks GitHub for priority issues, spawns Fargate tasks (always free tier)
- **Fargate Spot**: Runs Claude Code worker in containerized environment (pay per second)

**Key Advantages**:
- Only pay for actual processing time (not idle time)
- Auto-scales automatically
- No server management required
- Claude Code runs with 4GB RAM (sufficient resources)

---

## Cost Breakdown

| Component | Usage | Monthly Cost |
|-----------|-------|--------------|
| EventBridge | 8,640 triggers (every 5 min) | $0.00 (free) |
| Lambda | 8,640 invocations @ 128MB, 3 sec | $0.00 (free tier) |
| Fargate Spot | 50 hrs/month @ 1 vCPU + 4GB | $2.04 |
| CloudWatch Logs | 3GB/month | $0.00 (always free) |
| ECR (image storage) | <500MB | $0.00 (free tier) |
| **Total** | | **~$2-6/month** |

**Activity-based pricing**:
- 10 tasks/day @ 10 min each: $2.04/month
- 30 tasks/day @ 10 min each: $6.12/month
- 100 tasks/day @ 10 min each: $20.40/month

---

## Prerequisites

### 1. AWS Account

**Option A: New AWS Account (RECOMMENDED)**
- Sign up at https://aws.amazon.com
- Complete 5 onboarding tasks to get $200 credits
- Credits cover costs for 6+ months

**Option B: Existing AWS Account**
- Ensure billing is set up
- Set up budget alerts ($10/month threshold)

### 2. Local Development Tools

Install the following on your development machine:

```bash
# AWS CLI (for deployment)
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip
sudo ./aws/install

# Docker (for building container images)
# Ubuntu/Debian:
sudo apt-get update
sudo apt-get install docker.io
sudo systemctl start docker
sudo usermod -aG docker $USER  # Log out and back in after this

# Verify installations
aws --version
docker --version
```

### 3. AWS Credentials Configuration

```bash
# Configure AWS CLI with your credentials
aws configure

# Enter when prompted:
# - AWS Access Key ID: [Your access key]
# - AWS Secret Access Key: [Your secret key]
# - Default region: us-east-1
# - Default output format: json
```

**To create access keys**:
1. Go to AWS Console → IAM → Users → Your user
2. Security credentials tab → Create access key
3. Choose "CLI" use case
4. Save the access key ID and secret access key

### 4. GitHub Token

You already have this, but ensure it has the following permissions:
- `repo` (full repository access)
- `workflow` (if using GitHub Actions)

Store as environment variable:
```bash
export GITHUB_TOKEN="ghp_your_token_here"
```

---

## Codebase Changes Required

### 1. Create Dockerfile for Orchestrator

Create `Dockerfile` in the repository root:

```dockerfile
# Dockerfile
FROM public.ecr.aws/docker/library/node:18-slim

# Install Python 3.11 and system dependencies
RUN apt-get update && apt-get install -y \
    python3.11 \
    python3-pip \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI globally
RUN npm install -g @anthropic-ai/claude-code

# Set working directory
WORKDIR /app

# Copy Python dependencies
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy orchestrator code
COPY src/ ./src/

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV GITHUB_TOKEN=""

# Run orchestrator in one-shot mode (process one issue and exit)
CMD ["python3", "src/orchestrator/orchestrator_oneshot.py"]
```

**Key changes from current setup**:
- Uses `node:18-slim` base image (includes Node.js for Claude Code)
- Installs Python 3.11 alongside Node.js
- Installs Claude Code CLI via npm
- Runs orchestrator in one-shot mode (see next section)

### 2. Create One-Shot Orchestrator Script

Create `src/orchestrator/orchestrator_oneshot.py`:

```python
#!/usr/bin/env python3
"""
One-Shot Orchestrator for Serverless Deployment

Processes a single GitHub issue and exits. Designed for Fargate tasks.
Lambda spawns this container when work is detected.

Differences from continuous orchestrator:
- Processes ONE issue and exits (no infinite loop)
- Simplified error handling (Fargate handles retries)
- Logs to stdout/stderr (captured by CloudWatch)
"""
import os
import subprocess
import sys
from datetime import datetime
from urllib.parse import quote

try:
    import requests
except ImportError:
    print("[ERROR] requests library not found. Install with: pip install requests")
    sys.exit(1)

# Configuration
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_OWNER = os.getenv("REPO_OWNER", "bennetwi92")
REPO_NAME = os.getenv("REPO_NAME", "SolaraAlpha")
BASE_URL = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}"
HEADERS = {"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}

# Worker configuration
WORKER_TIMEOUT = 1200  # 20 minutes
MAX_COMMENT_LENGTH = 65000


def fetch_highest_priority_issue():
    """
    Fetch the highest priority issue from GitHub.
    Returns the first issue found (priority:high or priority:medium, no status label).
    """
    if not GITHUB_TOKEN:
        print("[ERROR] GITHUB_TOKEN environment variable not set")
        return None

    try:
        params = {
            "state": "open",
            "per_page": 100,
            "sort": "created",
            "direction": "asc",
        }

        response = requests.get(f"{BASE_URL}/issues", headers=HEADERS, params=params)
        response.raise_for_status()
        all_issues = response.json()

        priority_labels = {"priority:high", "priority:medium"}

        for issue in all_issues:
            issue_labels = {label["name"] for label in issue.get("labels", [])}

            has_priority = bool(issue_labels & priority_labels)
            has_task_type = "type:task" in issue_labels
            has_status = any(label.startswith("status:") for label in issue_labels)
            is_pr = "pull_request" in issue

            if has_priority and has_task_type and not has_status and not is_pr:
                # Return first matching issue (highest priority, oldest first)
                return issue

        return None

    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Failed to fetch issues: {e}")
        return None


def remove_status_labels(issue_num):
    """Remove all status: labels from a GitHub issue."""
    try:
        url = f"{BASE_URL}/issues/{issue_num}"
        response = requests.get(url, headers=HEADERS)
        response.raise_for_status()
        issue = response.json()

        status_labels = [
            label["name"]
            for label in issue.get("labels", [])
            if label["name"].startswith("status:")
        ]

        for label in status_labels:
            encoded_label = quote(label, safe='')
            delete_url = f"{BASE_URL}/issues/{issue_num}/labels/{encoded_label}"
            response = requests.delete(delete_url, headers=HEADERS)
            response.raise_for_status()

        return True
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Failed to remove status labels from issue #{issue_num}: {e}")
        return False


def update_issue_status(issue_num, new_status):
    """Update issue status label."""
    try:
        remove_status_labels(issue_num)
        url = f"{BASE_URL}/issues/{issue_num}/labels"
        response = requests.post(url, headers=HEADERS, json={"labels": [new_status]})
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Failed to update status label on issue #{issue_num}: {e}")
        return False


def post_issue_comment(issue_num, comment):
    """Post a comment to a GitHub issue."""
    try:
        url = f"{BASE_URL}/issues/{issue_num}/comments"
        response = requests.post(url, headers=HEADERS, json={"body": comment})
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Failed to post comment to issue #{issue_num}: {e}")
        return False


def spawn_claude_worker(issue):
    """Spawn Claude Code CLI to process an issue."""
    issue_num = issue["number"]
    issue_title = issue["title"]
    issue_body = issue.get("body", "")

    print(f"\n[START] Issue #{issue_num}: {issue_title}")
    print(f"[START] URL: {issue['html_url']}")

    # Update status to in-progress
    print("[LABEL] Updating status → in-progress...")
    if not update_issue_status(issue_num, "status:in-progress"):
        print("[WARNING] Could not update status to in-progress, continuing anyway...")

    prompt = f"{issue_body}"

    try:
        print(f"[WORKER] Starting Claude Code for issue #{issue_num}...")
        start_time = datetime.now()

        result = subprocess.run(
            ["claude", "-p", "--dangerously-skip-permissions", prompt],
            timeout=WORKER_TIMEOUT,
            capture_output=True,
            text=True,
        )

        elapsed = (datetime.now() - start_time).total_seconds()
        print(f"[WORKER] Completed in {elapsed:.1f}s with exit code {result.returncode}")

        # Combine stdout and stderr
        output_log = ""
        if result.stdout:
            output_log += result.stdout
        if result.stderr:
            if output_log:
                output_log += "\n\n--- STDERR ---\n\n"
            output_log += result.stderr

        # Truncate if exceeds GitHub comment limit
        if len(output_log) > MAX_COMMENT_LENGTH:
            output_log = output_log[:MAX_COMMENT_LENGTH] + "\n\n[OUTPUT TRUNCATED]"

        # Post output as comment
        if output_log.strip():
            comment_body = f"""## Fargate Worker Execution Log

**Duration**: {elapsed:.1f}s
**Exit Code**: {result.returncode}
**Status**: {'✅ Success' if result.returncode == 0 else '❌ Failed'}

<details>
<summary>View full execution log</summary>

```
{output_log}
```

</details>
"""
            print(f"[COMMENT] Posting execution log to issue #{issue_num}...")
            post_issue_comment(issue_num, comment_body)

        if result.returncode == 0:
            print(f"[SUCCESS] Issue #{issue_num} completed successfully")
            remove_status_labels(issue_num)
            return True
        else:
            print(f"[FAILED] Issue #{issue_num} failed with exit code {result.returncode}")
            update_issue_status(issue_num, "status:blocked")
            return False

    except subprocess.TimeoutExpired:
        print(f"[TIMEOUT] Issue #{issue_num} exceeded {WORKER_TIMEOUT}s timeout")
        timeout_comment = f"""## Fargate Worker Execution Log

**Duration**: {WORKER_TIMEOUT}s (timeout)
**Status**: ⏱️ Timeout

Worker exceeded maximum execution time and was terminated.
"""
        post_issue_comment(issue_num, timeout_comment)
        update_issue_status(issue_num, "status:blocked")
        return False
    except Exception as e:
        print(f"[ERROR] Issue #{issue_num} failed with error: {e}")
        error_comment = f"""## Fargate Worker Execution Log

**Status**: ❌ Error

Worker failed with exception:
```
{str(e)}
```
"""
        post_issue_comment(issue_num, error_comment)
        update_issue_status(issue_num, "status:blocked")
        return False


def main():
    """Main entry point - process one issue and exit."""
    print("=" * 80)
    print("SolaraAlpha One-Shot Orchestrator (Fargate Mode)")
    print(f"Repository: {REPO_OWNER}/{REPO_NAME}")
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    if not GITHUB_TOKEN:
        print("[ERROR] GITHUB_TOKEN environment variable not set")
        sys.exit(1)

    # Fetch highest priority issue
    print("\n[FETCH] Checking for priority issues...")
    issue = fetch_highest_priority_issue()

    if not issue:
        print("[NO WORK] No eligible issues found, exiting cleanly")
        sys.exit(0)

    # Process the issue
    print(f"\n[PROCESS] Found issue #{issue['number']}: {issue['title']}")
    success = spawn_claude_worker(issue)

    # Exit with appropriate code
    if success:
        print("\n[EXIT] Task completed successfully")
        sys.exit(0)
    else:
        print("\n[EXIT] Task failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
```

**Key differences from continuous orchestrator**:
- Processes ONE issue and exits (no loop)
- Exit code 0 = success, 1 = failure (Fargate uses this for retries)
- Simplified - no CEO poke, no idle sleep
- Designed to be spawned on-demand by Lambda

### 3. Create Lambda Function Code

Create `lambda/orchestrator_trigger.py`:

```python
"""
Lambda Function: Orchestrator Trigger

Triggered by EventBridge every 5 minutes.
Checks GitHub for priority issues and spawns Fargate tasks if work is found.
"""
import json
import os
import boto3

ecs_client = boto3.client('ecs')

# Configuration from environment variables
CLUSTER_NAME = os.environ['ECS_CLUSTER_NAME']
TASK_DEFINITION = os.environ['ECS_TASK_DEFINITION']
SUBNET_IDS = os.environ['SUBNET_IDS'].split(',')  # Comma-separated
SECURITY_GROUP_ID = os.environ['SECURITY_GROUP_ID']
GITHUB_TOKEN_SECRET_ARN = os.environ['GITHUB_TOKEN_SECRET_ARN']


def check_github_for_work():
    """
    Check GitHub for priority issues without status labels.
    Returns True if work is found, False otherwise.
    """
    import requests

    # Get GitHub token from environment (Lambda gets it from Secrets Manager)
    github_token = os.environ.get('GITHUB_TOKEN')
    if not github_token:
        print("[ERROR] GITHUB_TOKEN not available")
        return False

    repo_owner = "bennetwi92"
    repo_name = "SolaraAlpha"
    base_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}"
    headers = {"Authorization": f"token {github_token}"}

    try:
        params = {
            "state": "open",
            "per_page": 10,  # Only check first 10 issues
            "sort": "created",
            "direction": "asc",
        }

        response = requests.get(f"{base_url}/issues", headers=headers, params=params)
        response.raise_for_status()
        issues = response.json()

        priority_labels = {"priority:high", "priority:medium"}

        for issue in issues:
            issue_labels = {label["name"] for label in issue.get("labels", [])}

            has_priority = bool(issue_labels & priority_labels)
            has_task_type = "type:task" in issue_labels
            has_status = any(label.startswith("status:") for label in issue_labels)
            is_pr = "pull_request" in issue

            if has_priority and has_task_type and not has_status and not is_pr:
                print(f"[WORK FOUND] Issue #{issue['number']}: {issue['title']}")
                return True

        print("[NO WORK] No eligible issues found")
        return False

    except Exception as e:
        print(f"[ERROR] Failed to check GitHub: {e}")
        return False


def spawn_fargate_task():
    """Launch a Fargate Spot task to process the issue."""
    try:
        response = ecs_client.run_task(
            cluster=CLUSTER_NAME,
            taskDefinition=TASK_DEFINITION,
            launchType='FARGATE',
            capacityProviderStrategy=[
                {
                    'capacityProvider': 'FARGATE_SPOT',
                    'weight': 1,
                    'base': 0
                }
            ],
            networkConfiguration={
                'awsvpcConfiguration': {
                    'subnets': SUBNET_IDS,
                    'securityGroups': [SECURITY_GROUP_ID],
                    'assignPublicIp': 'ENABLED'
                }
            },
            overrides={
                'containerOverrides': [
                    {
                        'name': 'orchestrator',
                        'environment': [
                            {
                                'name': 'GITHUB_TOKEN',
                                'value': os.environ.get('GITHUB_TOKEN', '')
                            }
                        ]
                    }
                ]
            }
        )

        task_arn = response['tasks'][0]['taskArn']
        print(f"[FARGATE] Launched task: {task_arn}")
        return True

    except Exception as e:
        print(f"[ERROR] Failed to launch Fargate task: {e}")
        return False


def lambda_handler(event, context):
    """
    Lambda handler function.

    Triggered by EventBridge every 5 minutes.
    Checks GitHub for work and spawns Fargate task if needed.
    """
    print("[TRIGGER] Lambda triggered by EventBridge")

    # Check if there's work to do
    has_work = check_github_for_work()

    if not has_work:
        print("[IDLE] No work found, exiting cleanly")
        return {
            'statusCode': 200,
            'body': json.dumps({'message': 'No work found'})
        }

    # Spawn Fargate task
    print("[SPAWN] Work found, launching Fargate task...")
    success = spawn_fargate_task()

    if success:
        return {
            'statusCode': 200,
            'body': json.dumps({'message': 'Fargate task launched successfully'})
        }
    else:
        return {
            'statusCode': 500,
            'body': json.dumps({'message': 'Failed to launch Fargate task'})
        }
```

**What this Lambda does**:
1. Checks GitHub API for priority issues (quick check, <3 seconds)
2. If work found: Spawns Fargate Spot task
3. If no work: Exits cleanly (no cost beyond Lambda invocation)

---

## AWS Infrastructure Setup

### Step 1: Create ECR Repository

```bash
# Create repository for Docker image
aws ecr create-repository \
    --repository-name solara-orchestrator \
    --region us-east-1

# Output will include "repositoryUri" - save this
# Example: 123456789012.dkr.ecr.us-east-1.amazonaws.com/solara-orchestrator
```

### Step 2: Build and Push Docker Image

```bash
# Navigate to repository root
cd /path/to/SolaraAlpha

# Authenticate Docker to ECR
aws ecr get-login-password --region us-east-1 | \
    docker login --username AWS --password-stdin \
    123456789012.dkr.ecr.us-east-1.amazonaws.com

# Build Docker image
docker build -t solara-orchestrator .

# Tag image for ECR
docker tag solara-orchestrator:latest \
    123456789012.dkr.ecr.us-east-1.amazonaws.com/solara-orchestrator:latest

# Push to ECR
docker push 123456789012.dkr.ecr.us-east-1.amazonaws.com/solara-orchestrator:latest
```

### Step 3: Create Secrets in AWS Secrets Manager

```bash
# Store GitHub token
aws secretsmanager create-secret \
    --name solara/github-token \
    --secret-string "$GITHUB_TOKEN" \
    --region us-east-1

# Output will include "ARN" - save this
# Example: arn:aws:secretsmanager:us-east-1:123456789012:secret:solara/github-token-AbCdEf
```

### Step 4: Create IAM Roles

**ECS Task Execution Role** (allows Fargate to pull image and access secrets):

```bash
# Create trust policy file
cat > task-execution-trust-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "ecs-tasks.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF

# Create role
aws iam create-role \
    --role-name SolaraFargateExecutionRole \
    --assume-role-policy-document file://task-execution-trust-policy.json

# Attach AWS managed policy for ECS task execution
aws iam attach-role-policy \
    --role-name SolaraFargateExecutionRole \
    --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy

# Create custom policy for Secrets Manager access
cat > secrets-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "secretsmanager:GetSecretValue"
      ],
      "Resource": "arn:aws:secretsmanager:us-east-1:123456789012:secret:solara/github-token-*"
    }
  ]
}
EOF

aws iam put-role-policy \
    --role-name SolaraFargateExecutionRole \
    --policy-name SecretsManagerAccess \
    --policy-document file://secrets-policy.json
```

**ECS Task Role** (allows container to access AWS services - logs, S3, etc.):

```bash
# Create task role
aws iam create-role \
    --role-name SolaraFargateTaskRole \
    --assume-role-policy-document file://task-execution-trust-policy.json

# Create policy for CloudWatch Logs
cat > task-role-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:us-east-1:123456789012:log-group:/ecs/solara-orchestrator:*"
    }
  ]
}
EOF

aws iam put-role-policy \
    --role-name SolaraFargateTaskRole \
    --policy-name CloudWatchLogsAccess \
    --policy-document file://task-role-policy.json
```

**Lambda Execution Role**:

```bash
# Create trust policy for Lambda
cat > lambda-trust-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "lambda.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF

# Create role
aws iam create-role \
    --role-name SolaraLambdaExecutionRole \
    --assume-role-policy-document file://lambda-trust-policy.json

# Attach AWS managed policy for basic Lambda execution
aws iam attach-role-policy \
    --role-name SolaraLambdaExecutionRole \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

# Create custom policy for ECS task launching and Secrets Manager
cat > lambda-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ecs:RunTask",
        "ecs:DescribeTasks"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "secretsmanager:GetSecretValue"
      ],
      "Resource": "arn:aws:secretsmanager:us-east-1:123456789012:secret:solara/github-token-*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "iam:PassRole"
      ],
      "Resource": [
        "arn:aws:iam::123456789012:role/SolaraFargateExecutionRole",
        "arn:aws:iam::123456789012:role/SolaraFargateTaskRole"
      ]
    }
  ]
}
EOF

aws iam put-role-policy \
    --role-name SolaraLambdaExecutionRole \
    --policy-name ECSAndSecretsAccess \
    --policy-document file://lambda-policy.json
```

### Step 5: Create VPC Resources (if needed)

If you don't have a VPC, create one:

```bash
# Create VPC
aws ec2 create-vpc --cidr-block 10.0.0.0/16 --region us-east-1

# Create subnet (save subnet ID)
aws ec2 create-subnet \
    --vpc-id vpc-xxxxx \
    --cidr-block 10.0.1.0/24 \
    --availability-zone us-east-1a

# Create internet gateway
aws ec2 create-internet-gateway

# Attach to VPC
aws ec2 attach-internet-gateway \
    --vpc-id vpc-xxxxx \
    --internet-gateway-id igw-xxxxx

# Create route table and add route
aws ec2 create-route-table --vpc-id vpc-xxxxx
aws ec2 create-route \
    --route-table-id rtb-xxxxx \
    --destination-cidr-block 0.0.0.0/0 \
    --gateway-id igw-xxxxx

# Associate route table with subnet
aws ec2 associate-route-table \
    --subnet-id subnet-xxxxx \
    --route-table-id rtb-xxxxx
```

Or use default VPC (simpler):
```bash
# List default VPC and subnets
aws ec2 describe-vpcs --filters "Name=isDefault,Values=true"
aws ec2 describe-subnets --filters "Name=vpc-id,Values=vpc-xxxxx"
```

### Step 6: Create Security Group

```bash
# Create security group
aws ec2 create-security-group \
    --group-name solara-fargate-sg \
    --description "Security group for Solara Fargate tasks" \
    --vpc-id vpc-xxxxx

# Add outbound rule (Fargate needs HTTPS for GitHub API, Anthropic API, Docker pull)
aws ec2 authorize-security-group-egress \
    --group-id sg-xxxxx \
    --protocol tcp \
    --port 443 \
    --cidr 0.0.0.0/0

aws ec2 authorize-security-group-egress \
    --group-id sg-xxxxx \
    --protocol tcp \
    --port 80 \
    --cidr 0.0.0.0/0
```

### Step 7: Create CloudWatch Log Group

```bash
aws logs create-log-group \
    --log-group-name /ecs/solara-orchestrator \
    --region us-east-1
```

### Step 8: Create ECS Cluster

```bash
aws ecs create-cluster \
    --cluster-name solara-cluster \
    --region us-east-1
```

### Step 9: Register ECS Task Definition

Create `task-definition.json`:

```json
{
  "family": "solara-orchestrator",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "1024",
  "memory": "4096",
  "executionRoleArn": "arn:aws:iam::123456789012:role/SolaraFargateExecutionRole",
  "taskRoleArn": "arn:aws:iam::123456789012:role/SolaraFargateTaskRole",
  "containerDefinitions": [
    {
      "name": "orchestrator",
      "image": "123456789012.dkr.ecr.us-east-1.amazonaws.com/solara-orchestrator:latest",
      "essential": true,
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/solara-orchestrator",
          "awslogs-region": "us-east-1",
          "awslogs-stream-prefix": "ecs"
        }
      },
      "secrets": [
        {
          "name": "GITHUB_TOKEN",
          "valueFrom": "arn:aws:secretsmanager:us-east-1:123456789012:secret:solara/github-token-AbCdEf"
        }
      ],
      "environment": [
        {
          "name": "REPO_OWNER",
          "value": "bennetwi92"
        },
        {
          "name": "REPO_NAME",
          "value": "SolaraAlpha"
        }
      ]
    }
  ]
}
```

Register the task definition:
```bash
aws ecs register-task-definition \
    --cli-input-json file://task-definition.json \
    --region us-east-1
```

### Step 10: Create Lambda Function

```bash
# Package Lambda code
cd lambda
pip install requests -t .
zip -r lambda-function.zip .

# Create Lambda function
aws lambda create-function \
    --function-name solara-orchestrator-trigger \
    --runtime python3.11 \
    --role arn:aws:iam::123456789012:role/SolaraLambdaExecutionRole \
    --handler orchestrator_trigger.lambda_handler \
    --zip-file fileb://lambda-function.zip \
    --timeout 30 \
    --memory-size 128 \
    --environment Variables="{
        ECS_CLUSTER_NAME=solara-cluster,
        ECS_TASK_DEFINITION=solara-orchestrator,
        SUBNET_IDS=subnet-xxxxx,
        SECURITY_GROUP_ID=sg-xxxxx,
        GITHUB_TOKEN_SECRET_ARN=arn:aws:secretsmanager:us-east-1:123456789012:secret:solara/github-token-AbCdEf
    }" \
    --region us-east-1
```

### Step 11: Create EventBridge Rule

```bash
# Create rule to trigger every 5 minutes
aws events put-rule \
    --name solara-orchestrator-schedule \
    --schedule-expression "rate(5 minutes)" \
    --state ENABLED \
    --region us-east-1

# Add Lambda as target
aws events put-targets \
    --rule solara-orchestrator-schedule \
    --targets "Id"="1","Arn"="arn:aws:lambda:us-east-1:123456789012:function:solara-orchestrator-trigger" \
    --region us-east-1

# Grant EventBridge permission to invoke Lambda
aws lambda add-permission \
    --function-name solara-orchestrator-trigger \
    --statement-id AllowEventBridgeInvoke \
    --action lambda:InvokeFunction \
    --principal events.amazonaws.com \
    --source-arn arn:aws:events:us-east-1:123456789012:rule/solara-orchestrator-schedule \
    --region us-east-1
```

---

## Testing and Verification

### Test 1: Verify Docker Image Locally

```bash
# Run container locally to test
docker run --rm \
    -e GITHUB_TOKEN="$GITHUB_TOKEN" \
    -e REPO_OWNER="bennetwi92" \
    -e REPO_NAME="SolaraAlpha" \
    solara-orchestrator:latest

# Should process one issue and exit
```

### Test 2: Test Lambda Function

```bash
# Invoke Lambda manually
aws lambda invoke \
    --function-name solara-orchestrator-trigger \
    --region us-east-1 \
    output.json

# Check output
cat output.json
```

### Test 3: Monitor CloudWatch Logs

```bash
# Watch Lambda logs
aws logs tail /aws/lambda/solara-orchestrator-trigger --follow

# Watch Fargate logs
aws logs tail /ecs/solara-orchestrator --follow
```

### Test 4: Verify Fargate Task Execution

```bash
# List running tasks
aws ecs list-tasks \
    --cluster solara-cluster \
    --region us-east-1

# Describe specific task
aws ecs describe-tasks \
    --cluster solara-cluster \
    --tasks arn:aws:ecs:us-east-1:123456789012:task/xxxxx \
    --region us-east-1
```

---

## Monitoring and Maintenance

### CloudWatch Dashboards

Create a dashboard to monitor:
- Lambda invocations and errors
- Fargate task successes/failures
- GitHub API rate limit remaining
- Cost (using Cost Explorer)

### Cost Monitoring

```bash
# Set up budget alert
aws budgets create-budget \
    --account-id 123456789012 \
    --budget file://budget.json

# budget.json:
{
  "BudgetName": "SolaraMonthly",
  "BudgetLimit": {
    "Amount": "10",
    "Unit": "USD"
  },
  "TimeUnit": "MONTHLY",
  "BudgetType": "COST"
}
```

### Updating the Docker Image

When you make code changes:

```bash
# Rebuild image
docker build -t solara-orchestrator .

# Re-tag and push
docker tag solara-orchestrator:latest \
    123456789012.dkr.ecr.us-east-1.amazonaws.com/solara-orchestrator:latest
docker push 123456789012.dkr.ecr.us-east-1.amazonaws.com/solara-orchestrator:latest

# Fargate will automatically pull latest image on next task launch
```

---

## Troubleshooting

### Issue: Fargate task fails to start

**Symptoms**: Task goes to "STOPPED" state immediately

**Diagnosis**:
```bash
aws ecs describe-tasks \
    --cluster solara-cluster \
    --tasks <task-arn> \
    --region us-east-1
```

**Common causes**:
- Docker image not found in ECR (check image URI)
- Insufficient permissions (check execution role)
- Invalid secrets ARN (verify Secrets Manager ARN)
- No available subnets (check VPC configuration)

### Issue: Claude Code authentication fails

**Symptoms**: Task logs show "Claude authentication required"

**Solution**: Claude Code requires manual authentication first. Two options:

1. **Use Anthropic API directly** (recommended for serverless):
   - Modify orchestrator to use Anthropic API instead of Claude Code CLI
   - Store API key in Secrets Manager

2. **Pre-authenticate in Docker image** (complex):
   - Run `claude --login` during image build
   - Store authentication token in image (not recommended for security)

### Issue: Lambda timeout

**Symptoms**: Lambda returns timeout error after 30 seconds

**Solution**: Lambda only checks GitHub (fast). If timing out:
- Increase Lambda timeout: `aws lambda update-function-configuration --function-name solara-orchestrator-trigger --timeout 60`
- Check GitHub API rate limit
- Verify network connectivity

### Issue: High costs

**Symptoms**: AWS bill higher than expected

**Diagnosis**:
```bash
# Check Fargate usage
aws ce get-cost-and-usage \
    --time-period Start=2025-01-01,End=2025-01-31 \
    --granularity MONTHLY \
    --metrics UnblendedCost \
    --filter file://fargate-filter.json
```

**Solutions**:
- Review Fargate task run time (check CloudWatch Logs)
- Ensure tasks are terminating properly (not hanging)
- Consider reducing polling frequency (10 minutes instead of 5)
- Switch to EC2 Spot if processing >8 hours/day

---

## Migration from Local to Serverless

### Phase 1: Run both in parallel (1 week)
- Deploy serverless alongside local orchestrator
- Local handles production
- Serverless handles test issues (use label filter)

### Phase 2: Switch to serverless (after validation)
- Stop local orchestrator
- Monitor serverless for 1 week
- Keep local as backup

### Phase 3: Full serverless (stable)
- Remove local orchestrator setup
- Focus on serverless optimization

---

## Summary

**Total Setup Time**: 4-6 hours (first time), 1-2 hours (subsequent deploys)

**Monthly Cost**: $2-6 for Phase 1 (10-30 tasks/day)

**Key Benefits**:
- Pay only for actual processing time
- Auto-scales automatically
- No server management
- Professional monitoring included

**Next Steps After Setup**:
1. Monitor CloudWatch Logs for 1 week
2. Verify GitHub issues are being processed correctly
3. Set up billing alerts
4. Optimize polling frequency based on activity
5. Consider migrating to Anthropic API directly (avoid Claude Code CLI overhead)
