# S3 single-line file processor (AWS CDK, Python)

S3 bucket -> `ObjectCreated` notification -> Python 3.14 Lambda that parses the one-line file.

```
app.py                         CDK entry point
s3_event_processor/stack.py    Bucket, bucket policy, Lambda, event notification
lambda_src/handler.py          Lambda code
tests/                         Handler unit tests (stdlib) + stack assertions (pytest)
.github/workflows/deploy.yml   CI/CD: test and deploy on push to main
scripts/create-github-oidc-role.sh  One-time IAM role setup for GitHub -> AWS OIDC
```

## Deploy

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
npm install -g aws-cdk            # CDK CLI
cdk bootstrap                     # once per account/region
cdk deploy
```

## Try it

```bash
BUCKET=$(aws cloudformation describe-stacks --stack-name S3EventProcessorStack \
  --query "Stacks[0].Outputs[?OutputKey=='BucketName'].OutputValue" --output text)

echo '{"id": 42, "name": "widget"}' > sample.json && aws s3 cp sample.json s3://$BUCKET/
echo '1,Alice,NYC'                  > sample.csv  && aws s3 cp sample.csv  s3://$BUCKET/

aws logs tail /aws/lambda/$(aws cloudformation describe-stacks --stack-name S3EventProcessorStack \
  --query "Stacks[0].Outputs[?OutputKey=='FunctionName'].OutputValue" --output text) --since 5m
```

## Test

```bash
python -m unittest tests.test_handler   # no dependencies needed
pytest                                  # includes stack assertions
```

## CI/CD (GitHub Actions)

`.github/workflows/deploy.yml` runs on every push to `main` (and manually via "Run workflow"). It installs
dependencies, runs the tests, then runs `cdk diff` and `cdk deploy`. If the tests fail, nothing is deployed.

It authenticates to AWS with GitHub's OIDC integration, so no AWS access keys are stored in GitHub.
Complete this one-time setup before the first run:

### 1. Bootstrap the AWS account

Run once per account/region, either locally or in AWS CloudShell (CloudShell is already signed in as your
console user and uses the region selected in the console). Bootstrapping doesn't need this project's code:

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=us-east-1   # change to the region you want to deploy to
npx aws-cdk@2 bootstrap aws://$ACCOUNT_ID/$REGION
```

Locally, if your credentials live in a named profile, run `export AWS_PROFILE=<profile>` first (or add
`--profile <profile>` to the commands). You need Node.js for `npx`; CloudShell already has it.

### 2. Create the IAM role that GitHub can assume

`scripts/create-github-oidc-role.sh` does this for you. It creates the GitHub OIDC provider (if missing),
creates a role that only trusts workflows on the `main` branch of `gpsiegel/case_study`, and lets that role
assume the CDK bootstrap roles. Run it with admin-level credentials, and note the role ARN it prints at the end.

**Locally:**

```bash
chmod +x scripts/create-github-oidc-role.sh
./scripts/create-github-oidc-role.sh
```

**In CloudShell:** create the file, make it executable, and run it:

```bash
nano create-github-oidc-role.sh   # paste the contents of scripts/create-github-oidc-role.sh, save with Ctrl+O, exit with Ctrl+X
chmod +x create-github-oidc-role.sh
./create-github-oidc-role.sh
```

(Alternatively, use CloudShell's Actions -> Upload file to upload it from your computer.)

To use a different repo or role name, set them inline:
`REPO=owner/repo ROLE_NAME=my-role ./create-github-oidc-role.sh`.
To grant more than the default permissions (for example `AdministratorAccess` on a throwaway account), attach
a policy to the role afterwards. Broader than needed, but handy for troubleshooting a permissions error.

### 3. Add settings in GitHub

Repo -> Settings -> Secrets and variables -> Actions:

| Type     | Name           | Value                                   |
| -------- | -------------- | --------------------------------------- |
| Secret   | `AWS_ROLE_ARN` | ARN of the role created in step 2       |
| Variable | `AWS_REGION`   | Region you bootstrapped, e.g. `us-east-1` |

### Notes

- If the project lives in a subfolder of the repo rather than the root, add
  `defaults: run: working-directory: <subfolder>` to the job, and point `cache-dependency-path` at that
  folder. The workflow file itself must stay in the repo's root `.github/workflows/` folder.
- The job's `concurrency` setting queues deploys so two never run against the stack at once.

## Clean up

`cdk destroy` (bucket is set to auto-delete its objects; switch to `RETAIN` for production).
