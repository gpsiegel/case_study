#!/usr/bin/env bash
#
# Creates the IAM role that the GitHub Actions deploy workflow assumes via OIDC.
#
#   1. Creates the GitHub OIDC identity provider (skipped if it already exists)
#   2. Creates the role, trusting only workflows on the main branch of REPO
#   3. Attaches a policy that lets the role assume the CDK bootstrap roles
#
# Run it with admin-level AWS credentials, locally or in AWS CloudShell.
# Optional overrides:  REPO=owner/repo ROLE_NAME=my-role ./create-github-oidc-role.sh
#
set -euo pipefail

REPO="${REPO:-gpsiegel/case_study}"
ROLE_NAME="${ROLE_NAME:-github-actions-cdk-deploy}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

echo "Account: ${ACCOUNT_ID}"
echo "Repo:    ${REPO} (main branch only)"
echo "Role:    ${ROLE_NAME}"
echo

# 1. GitHub OIDC provider ------------------------------------------------------
if aws iam list-open-id-connect-providers --query 'OpenIDConnectProviderList[].Arn' --output text \
     | grep -q "token.actions.githubusercontent.com"; then
  echo "OIDC provider already exists, skipping."
else
  aws iam create-open-id-connect-provider \
    --url https://token.actions.githubusercontent.com \
    --client-id-list sts.amazonaws.com >/dev/null
  echo "Created OIDC provider."
fi

# 2. Role ----------------------------------------------------------------------
cat > "${WORKDIR}/trust-policy.json" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::${ACCOUNT_ID}:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
          "token.actions.githubusercontent.com:sub": "repo:${REPO}:ref:refs/heads/main"
        }
      }
    }
  ]
}
EOF

aws iam create-role \
  --role-name "${ROLE_NAME}" \
  --assume-role-policy-document "file://${WORKDIR}/trust-policy.json" \
  --description "GitHub Actions role for deploying the CDK stack" >/dev/null
echo "Created role ${ROLE_NAME}."

# 3. Permissions: only assume the CDK bootstrap roles (they do the deploying) ---
cat > "${WORKDIR}/permissions-policy.json" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "sts:AssumeRole",
      "Resource": "arn:aws:iam::${ACCOUNT_ID}:role/cdk-*"
    },
    {
      "Effect": "Allow",
      "Action": "ssm:GetParameter",
      "Resource": "arn:aws:ssm:*:${ACCOUNT_ID}:parameter/cdk-bootstrap/*"
    }
  ]
}
EOF

aws iam put-role-policy \
  --role-name "${ROLE_NAME}" \
  --policy-name cdk-deploy \
  --policy-document "file://${WORKDIR}/permissions-policy.json"
echo "Attached cdk-deploy policy."

# Verify both pieces really exist; fail loudly if not.
aws iam get-role-policy --role-name "${ROLE_NAME}" --policy-name cdk-deploy >/dev/null \
  || { echo "ERROR: cdk-deploy policy is missing from ${ROLE_NAME}" >&2; exit 1; }
aws iam get-role --role-name "${ROLE_NAME}" >/dev/null \
  || { echo "ERROR: role ${ROLE_NAME} not found" >&2; exit 1; }
echo "Verified: role and cdk-deploy policy are in place."

echo
echo "Done. Save this value as the AWS_ROLE_ARN secret in GitHub:"
aws iam get-role --role-name "${ROLE_NAME}" --query Role.Arn --output text
