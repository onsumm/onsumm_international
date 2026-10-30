#!/usr/bin/env bash
# CloudFront in front of one environment's document bucket — signed URLs only.
#
#   bash setup_cdn.sh dev  cf-dev.pub.pem     # onsumm-assets-dev
#   bash setup_cdn.sh prod cf-prod.pub.pem    # onsumm-assets
#
# Documents never get a public URL. This distribution:
#   - serves only requests carrying a valid CloudFront signature (trusted key
#     group); the API signs a link per document after checking the tenant, and
#     it expires in SIGNED_URL_TTL seconds (5 min by default);
#   - is the only thing allowed to read the bucket (Origin Access Control +
#     bucket policy); the bucket itself stays fully private;
#   - does not cache: these are per-tenant documents opened by a handful of
#     people, and an edited attachment keeps its key, so a cache would only
#     risk serving the old version.
#
# The signing key pair is made ON THE SERVER, so the private key never leaves it:
#   mkdir -p ~/.onsumm && chmod 700 ~/.onsumm
#   openssl genrsa -out ~/.onsumm/cf-dev.pem 2048 && chmod 600 ~/.onsumm/cf-dev.pem
#   openssl rsa -in ~/.onsumm/cf-dev.pem -pubout -out ~/cf-dev.pub.pem
# Upload only cf-dev.pub.pem to CloudShell and run this script there.
set -euo pipefail

ENV="${1:?usage: setup_cdn.sh dev|prod <public-key.pem>}"
PUB="${2:?usage: setup_cdn.sh dev|prod <public-key.pem>}"
case "$ENV" in
  prod) BUCKET=onsumm-assets ;;
  dev)  BUCKET=onsumm-assets-dev ;;
  *) echo "env must be dev or prod" >&2; exit 1 ;;
esac
REGION=ap-south-1
NAME="onsumm-files-$ENV"
COMMENT="$NAME: $BUCKET, signed URLs only"
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
TS=$(date +%s)

# AWS-managed policies.
CACHING_DISABLED=4135ea2d-6df8-44a3-9df3-4b5a84be39ad
SIMPLE_CORS=60669652-455b-4ae9-85a4-c4c02393f86c   # lets the web app read the response; signature still required

EXISTING=$(aws cloudfront list-distributions \
  --query "DistributionList.Items[?Comment=='$COMMENT'].Id | [0]" --output text)
if [ "$EXISTING" != "None" ] && [ -n "$EXISTING" ]; then
  echo "distribution for $ENV already exists: $EXISTING — nothing to do"
  exit 0
fi

# Public key + key group: the signatures CloudFront will accept.
python3 - "$PUB" "$NAME" "$TS" > /tmp/pk.json <<'PY'
import json, sys
pem, name, ts = open(sys.argv[1]).read(), sys.argv[2], sys.argv[3]
print(json.dumps({"CallerReference": f"{name}-{ts}", "Name": f"{name}-{ts}", "EncodedKey": pem,
                  "Comment": "API signs document links with the matching private key"}))
PY
KEY_ID=$(aws cloudfront create-public-key --public-key-config file:///tmp/pk.json --query PublicKey.Id --output text)
KEY_GROUP=$(aws cloudfront create-key-group \
  --key-group-config "{\"Name\":\"$NAME-$TS\",\"Items\":[\"$KEY_ID\"]}" --query KeyGroup.Id --output text)

OAC=$(aws cloudfront create-origin-access-control --origin-access-control-config \
  "Name=$NAME-$TS,SigningProtocol=sigv4,SigningBehavior=always,OriginAccessControlOriginType=s3" \
  --query OriginAccessControl.Id --output text)

cat > /tmp/dist.json <<JSON
{
  "CallerReference": "$NAME-$TS",
  "Comment": "$COMMENT",
  "Enabled": true,
  "PriceClass": "PriceClass_200",
  "HttpVersion": "http2and3",
  "Origins": {"Quantity": 1, "Items": [{
    "Id": "s3-$BUCKET",
    "DomainName": "$BUCKET.s3.$REGION.amazonaws.com",
    "S3OriginConfig": {"OriginAccessIdentity": ""},
    "OriginAccessControlId": "$OAC"
  }]},
  "DefaultCacheBehavior": {
    "TargetOriginId": "s3-$BUCKET",
    "ViewerProtocolPolicy": "https-only",
    "AllowedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"],
                       "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]}},
    "Compress": true,
    "CachePolicyId": "$CACHING_DISABLED",
    "ResponseHeadersPolicyId": "$SIMPLE_CORS",
    "TrustedKeyGroups": {"Enabled": true, "Quantity": 1, "Items": ["$KEY_GROUP"]}
  }
}
JSON
read -r DIST_ID DOMAIN < <(aws cloudfront create-distribution --distribution-config file:///tmp/dist.json \
  --query "Distribution.[Id,DomainName]" --output text)

# Only this distribution may read the bucket. This is not a public policy, so
# the bucket's Block Public Access settings stay on.
cat > /tmp/policy.json <<JSON
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "CloudFrontSignedReadsOnly",
    "Effect": "Allow",
    "Principal": {"Service": "cloudfront.amazonaws.com"},
    "Action": "s3:GetObject",
    "Resource": "arn:aws:s3:::$BUCKET/*",
    "Condition": {"StringEquals": {"AWS:SourceArn": "arn:aws:cloudfront::$ACCOUNT:distribution/$DIST_ID"}}
  }]
}
JSON
aws s3api put-bucket-policy --bucket "$BUCKET" --policy file:///tmp/policy.json
rm -f /tmp/pk.json /tmp/dist.json /tmp/policy.json

cat <<EOF

CloudFront for $ENV is being deployed (takes ~5 minutes): $DIST_ID

Add to the $ENV checkout's .env, then restart its API:
  FILES_CDN_DOMAIN=$DOMAIN
  CLOUDFRONT_KEY_ID=$KEY_ID
  CLOUDFRONT_PRIVATE_KEY_PATH=/home/ec2-user/.onsumm/cf-$ENV.pem

Check it refuses unsigned access (expect 403):
  curl -s -o /dev/null -w '%{http_code}\n' https://$DOMAIN/anything
EOF
