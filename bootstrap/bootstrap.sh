#!/usr/bin/env bash
# One-time AWS setup. Run in AWS CloudShell (us-west-2) from the repo root:
#   bash bootstrap/bootstrap.sh <github-owner>/<repo>
# Creates: Terraform state bucket, GitHub OIDC provider, scoped deploy role for GitHub Actions.
# Safe to re-run.
set -euo pipefail

REPO="${1:?usage: bootstrap.sh <github-owner>/<repo>}"
REGION="${AWS_REGION:-us-west-2}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
STATE_BUCKET="rde-tfstate-${ACCOUNT_ID}"
ROLE_NAME="rde-github-deploy"
OIDC_URL="token.actions.githubusercontent.com"
OIDC_ARN="arn:aws:iam::${ACCOUNT_ID}:oidc-provider/${OIDC_URL}"
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "Account ${ACCOUNT_ID}, region ${REGION}, repo ${REPO}"

# 1. Terraform state bucket: private, versioned, encrypted
if ! aws s3api head-bucket --bucket "${STATE_BUCKET}" 2>/dev/null; then
  aws s3api create-bucket --bucket "${STATE_BUCKET}" --region "${REGION}" \
    --create-bucket-configuration LocationConstraint="${REGION}"
fi
aws s3api put-bucket-versioning --bucket "${STATE_BUCKET}" --versioning-configuration Status=Enabled
aws s3api put-public-access-block --bucket "${STATE_BUCKET}" --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-encryption --bucket "${STATE_BUCKET}" --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'

# 2. GitHub OIDC identity provider (no long-lived keys anywhere)
if ! aws iam get-open-id-connect-provider --open-id-connect-provider-arn "${OIDC_ARN}" >/dev/null 2>&1; then
  aws iam create-open-id-connect-provider --url "https://${OIDC_URL}" --client-id-list sts.amazonaws.com
fi

# 3. Deploy role: only this repo's main branch, its dev/prod environments and PRs may assume it
cat > /tmp/trust.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Federated": "${OIDC_ARN}"},
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {"${OIDC_URL}:aud": "sts.amazonaws.com"},
      "StringLike": {"${OIDC_URL}:sub": [
        "repo:${REPO}:ref:refs/heads/main",
        "repo:${REPO}:environment:dev",
        "repo:${REPO}:environment:prod",
        "repo:${REPO}:pull_request"
      ]}
    }
  }]
}
EOF
if aws iam get-role --role-name "${ROLE_NAME}" >/dev/null 2>&1; then
  aws iam update-assume-role-policy --role-name "${ROLE_NAME}" --policy-document file:///tmp/trust.json
else
  aws iam create-role --role-name "${ROLE_NAME}" --assume-role-policy-document file:///tmp/trust.json \
    --description "GitHub Actions deploy role for robot-data-engine" >/dev/null
fi
sed "s/ACCOUNT_ID/${ACCOUNT_ID}/g" "${HERE}/deploy-role-policy.json" > /tmp/deploy-policy.json
aws iam put-role-policy --role-name "${ROLE_NAME}" --policy-name rde-deploy \
  --policy-document file:///tmp/deploy-policy.json

echo
echo "Done. Add these to GitHub -> Settings -> Secrets and variables -> Actions -> Variables:"
echo "  AWS_ROLE_ARN     = arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"
echo "  AWS_REGION       = ${REGION}"
echo "  TF_STATE_BUCKET  = ${STATE_BUCKET}"
