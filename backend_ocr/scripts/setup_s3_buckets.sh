#!/usr/bin/env bash
# Create the document buckets and one IAM user per environment.
#
#   onsumm-assets      prod  (~/app/.env      S3_BUCKET=onsumm-assets)
#   onsumm-assets-dev  dev   (~/app-dev/.env  S3_BUCKET=onsumm-assets-dev)
#
# Prod and dev run on the same EC2 box, so an instance role would give dev the
# prod bucket too. Each environment instead gets its own IAM user, allowed only
# its own bucket; its access key goes in that checkout's .env.
#
# Run once with an admin AWS profile:  AWS_PROFILE=admin bash setup_s3_buckets.sh
# Safe to re-run: existing buckets/users are left as they are.
set -euo pipefail

REGION=ap-south-1

for ENV in prod dev; do
  if [ "$ENV" = prod ]; then BUCKET=onsumm-assets; else BUCKET=onsumm-assets-dev; fi
  USER="onsumm-api-$ENV"

  if aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
    echo "bucket $BUCKET exists"
  else
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
      --create-bucket-configuration LocationConstraint="$REGION"
    echo "created bucket $BUCKET"
  fi

  # Never public: a document is reached only through a short-lived link the API
  # signs after checking the tenant.
  aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
  aws s3api put-bucket-encryption --bucket "$BUCKET" --server-side-encryption-configuration \
    '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"},"BucketKeyEnabled":true}]}'
  # Versioned so an overwritten (edited) or deleted document can be recovered;
  # old versions are kept 90 days.
  aws s3api put-bucket-versioning --bucket "$BUCKET" --versioning-configuration Status=Enabled
  aws s3api put-bucket-lifecycle-configuration --bucket "$BUCKET" --lifecycle-configuration '{
    "Rules":[{"ID":"expire-old-versions","Status":"Enabled","Filter":{},
      "NoncurrentVersionExpiration":{"NoncurrentDays":90},
      "AbortIncompleteMultipartUpload":{"DaysAfterInitiation":7}}]}'

  # Lets the web app read a signed link it was given (the browser fetches the
  # file directly). CORS only decides which pages may read a response; every
  # request still needs a valid signature, so this opens nothing.
  aws s3api put-bucket-cors --bucket "$BUCKET" --cors-configuration '{
    "CORSRules":[{"AllowedMethods":["GET","HEAD"],
      "AllowedOrigins":["https://www.onsumm.ai","https://onsumm.ai","https://uat2.d179fc4xwm6zfv.amplifyapp.com","http://localhost:5173"],
      "AllowedHeaders":["*"],"MaxAgeSeconds":3600}]}'

  aws iam get-user --user-name "$USER" >/dev/null 2>&1 || aws iam create-user --user-name "$USER" >/dev/null
  aws iam put-user-policy --user-name "$USER" --policy-name "$BUCKET-rw" --policy-document "{
    \"Version\":\"2012-10-17\",
    \"Statement\":[
      {\"Effect\":\"Allow\",\"Action\":[\"s3:GetObject\",\"s3:PutObject\",\"s3:DeleteObject\"],
       \"Resource\":\"arn:aws:s3:::$BUCKET/*\"},
      {\"Effect\":\"Allow\",\"Action\":[\"s3:ListBucket\"],\"Resource\":\"arn:aws:s3:::$BUCKET\"}
    ]}"
  echo "user $USER can read/write $BUCKET"
done

cat <<'EOF'

Next, for each user (keys are shown once):
  aws iam create-access-key --user-name onsumm-api-prod   # -> ~/app/.env
  aws iam create-access-key --user-name onsumm-api-dev    # -> ~/app-dev/.env
and add to that .env:
  S3_BUCKET=onsumm-assets            (dev: onsumm-assets-dev)
  AWS_REGION=ap-south-1
  AWS_ACCESS_KEY_ID=...
  AWS_SECRET_ACCESS_KEY=...
EOF
