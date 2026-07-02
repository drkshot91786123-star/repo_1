#!/bin/bash
# Retries ARM instance launch until capacity is available

TENANCY="ocid1.tenancy.oc1..aaaaaaaaykdgnboer3tcfb4g26grkns4ddh6q3ba5higagf44inv3z4rvsna"
SUBNET="ocid1.subnet.oc1.ap-mumbai-1.aaaaaaaag7ztecm7ev2vmtr4lmsevqo2qe2h6j5owwh4wrgf663plmgyvkpa"
IMAGE="ocid1.image.oc1.ap-mumbai-1.aaaaaaaa2gm4bht5gaf4ubvmimpprarmnceguytmzrfufmqfmmvpgyul6z5a"
OCI=~/bin/oci
INTERVAL=300  # seconds between retries

echo "Starting retry loop. Will attempt every ${INTERVAL}s until success."
echo "Press Ctrl+C to stop."

while true; do
  TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
  echo -n "[$TIMESTAMP] Attempting launch... "

  RESULT=$($OCI compute instance launch \
    --compartment-id $TENANCY \
    --availability-domain "aPBV:AP-MUMBAI-1-AD-1" \
    --display-name "website-automation" \
    --image-id $IMAGE \
    --shape "VM.Standard.A1.Flex" \
    --shape-config '{"ocpus":4,"memoryInGBs":24}' \
    --subnet-id $SUBNET \
    --assign-public-ip true \
    --ssh-authorized-keys-file ~/.ssh/oracle_automation.pub \
    --boot-volume-size-in-gbs 50 2>&1)

  if echo "$RESULT" | grep -q '"lifecycle-state"' || echo "$RESULT" | grep -q 'PROVISIONING'; then
    echo "SUCCESS!"
    echo "$RESULT"
    break
  elif echo "$RESULT" | grep -q 'Out of host capacity'; then
    echo "no capacity, retrying in ${INTERVAL}s..."
  else
    echo "unexpected error:"
    echo "$RESULT"
    echo "Retrying in ${INTERVAL}s..."
  fi

  sleep $INTERVAL
done
