#!/bin/bash
# Comprehensive API Schema Generator for Retell
# Calls real NexHealth APIs to capture Request/Response patterns

OUTPUT_FILE="api_schemas_log.txt"
echo "Generating API Schemas..." > $OUTPUT_FILE

# Configuration
SUBDOMAIN="silora-demo-practice"
LOCATION_ID="339273" # Default, will try to fetch dynamic
# Auth is hardcoded from original script, assuming it's valid
AUTH_TOKEN="dXNlci0xMzA2LXNhbmRib3g.eo0TQORAig1lpVRvl75u2doxvTX1UKUO"

echo "================================================================================"
echo "1. Authentication"
echo "================================================================================"
# Note: Using the Basic auth token from original script to get a Bearer token
TOKEN_RESPONSE=$(curl -s -X POST https://nexhealth.info/authenticates \
  -H "Content-Type: application/json" \
  -H "Accept: application/vnd.nexhealth+json" \
  -H "Authorization: $AUTH_TOKEN" \
  -H "Nex-Api-Version: v2")

TOKEN=$(echo $TOKEN_RESPONSE | jq -r '.data.token')
if [ "$TOKEN" == "null" ] || [ -z "$TOKEN" ]; then
    echo "❌ Failed to get token"
    exit 1
fi
echo "✅ Got Bearer Token"

# Function to log and call
call_api() {
    NAME=$1
    METHOD=$2
    URL=$3
    DATA=$4
    
    echo -e "\n\n--------------------------------------------------------------------------------" >> $OUTPUT_FILE
    echo "API: $NAME" >> $OUTPUT_FILE
    echo "METHOD: $METHOD" >> $OUTPUT_FILE
    echo "URL: $URL" >> $OUTPUT_FILE
    if [ ! -z "$DATA" ]; then
        echo "BODY: $DATA" >> $OUTPUT_FILE
    fi
    echo "--------------------------------------------------------------------------------" >> $OUTPUT_FILE

    if [ "$METHOD" == "GET" ]; then
        RESPONSE=$(curl -s -X GET "$URL" \
          -H "Authorization: Bearer $TOKEN" \
          -H "Accept: application/vnd.nexhealth+json" \
          -H "Nex-Api-Version: v2")
    else
        RESPONSE=$(curl -s -X $METHOD "$URL" \
          -H "Authorization: Bearer $TOKEN" \
          -H "Accept: application/vnd.nexhealth+json" \
          -H "Content-Type: application/json" \
          -H "Nex-Api-Version: v2" \
          -d "$DATA")
    fi

    echo "RESPONSE:" >> $OUTPUT_FILE
    echo "$RESPONSE" | jq '.' >> $OUTPUT_FILE
    echo "$RESPONSE" # Return for parsing
}

# 2. List Locations
echo "2. Listing Locations..."
LOCS_RESP=$(call_api "List Locations" "GET" "https://nexhealth.info/locations?subdomain=$SUBDOMAIN&per_page=1" "")

# 3. List Patients (Search)
echo "3. Searching Patient..."
# Searching for a typically existing patient or just list
SEARCH_RESP=$(call_api "Search Patient" "GET" "https://nexhealth.info/patients?subdomain=$SUBDOMAIN&location_id=$LOCATION_ID&per_page=1" "")
PATIENT_ID=$(echo "$SEARCH_RESP" | jq -r '.data.patients[0].id // empty')

# 4. Create Patient
echo "4. Creating Test Patient..."
RANDOM_NUM=$RANDOM
NEW_EMAIL="retell_test_${RANDOM_NUM}@example.com"
CREATE_PAYLOAD=$(cat <<EOF
{
  "provider": { "provider_id": 12345 },
  "patient": {
    "first_name": "Test",
    "last_name": "RetellUser",
    "email": "$NEW_EMAIL",
    "bio": {
      "date_of_birth": "1990-01-01",
      "phone_number": "5555555555",
      "gender": "Female"
    }
  }
}
EOF
)
# Note: Creating patient usually needs a real provider ID. Let's fetch one first.
PROV_RESP=$(call_api "List Providers" "GET" "https://nexhealth.info/providers?subdomain=$SUBDOMAIN&location_id=$LOCATION_ID&per_page=1" "")
PROVIDER_ID=$(echo "$PROV_RESP" | jq -r '.data[0].id')
echo "   Using Provider ID: $PROVIDER_ID"

# Update payload with real provider
CREATE_PAYLOAD=$(echo "$CREATE_PAYLOAD" | jq --arg pid "$PROVIDER_ID" '.provider.provider_id = ($pid|tonumber)')

CREATE_RESP=$(call_api "Create Patient" "POST" "https://nexhealth.info/patients?subdomain=$SUBDOMAIN&location_id=$LOCATION_ID" "$CREATE_PAYLOAD")
NEW_PATIENT_ID=$(echo "$CREATE_RESP" | jq -r '.data.user.id // empty')

# Fallback: If creation failed (duplicate), parse ID from error or use Search result
if [ -z "$NEW_PATIENT_ID" ]; then
    echo "   ⚠️ Creation failed (likely duplicate). Trying to extract ID from error..."
    # Error format: "A patient with that information already exists - id=12345"
    NEW_PATIENT_ID=$(echo "$CREATE_RESP" | jq -r '.error[0]' | grep -o 'id=[0-9]*' | cut -d= -f2)
fi

if [ -z "$NEW_PATIENT_ID" ]; then
     echo "   ⚠️ Still no ID. Using Searched Patient ID: $PATIENT_ID"
     NEW_PATIENT_ID=$PATIENT_ID
fi

echo "   Patient ID to use: $NEW_PATIENT_ID"

# 5. Get Appointment Slots
echo "5. Finding Slots..."
# Find slots for tomorrow
TOMORROW=$(date -v+1d +%Y-%m-%d)
SLOTS_RESP=$(call_api "Find Slots" "GET" "https://nexhealth.info/appointment_slots?subdomain=$SUBDOMAIN&lids[]=$LOCATION_ID&start_date=$TOMORROW&days=1&pids[]=$PROVIDER_ID" "")
# Extract a valid start time and operatory
SLOT_TIME=$(echo "$SLOTS_RESP" | jq -r '.data[0].slots[0].time // empty')
OPERATORY_ID=$(echo "$SLOTS_RESP" | jq -r '.data[0].slots[0].operatory_id // empty')
echo "   Found Slot: $SLOT_TIME (Operatory: $OPERATORY_ID)"

# 6. Book Appointment
if [ ! -z "$SLOT_TIME" ] && [ ! -z "$NEW_PATIENT_ID" ]; then
    echo "6. Booking Appointment..."
    BOOK_PAYLOAD=$(cat <<EOF
{
  "appt": {
    "start_time": "$SLOT_TIME",
    "patient_id": $NEW_PATIENT_ID,
    "provider_id": $PROVIDER_ID,
    "location_id": $LOCATION_ID,
    "operatory_id": $OPERATORY_ID
  }
}
EOF
)
    BOOK_RESP=$(call_api "Book Appointment" "POST" "https://nexhealth.info/appointments?subdomain=$SUBDOMAIN&location_id=$LOCATION_ID&notify_patient=false" "$BOOK_PAYLOAD")
    APPT_ID=$(echo "$BOOK_RESP" | jq -r '.data.appt.id // empty')
    echo "   Booked Appt ID: $APPT_ID"
    
    # 7. Cancel Appointment
    if [ ! -z "$APPT_ID" ]; then
        echo "7. Cancelling Appointment..."
        CANCEL_PAYLOAD='{"appt":{"cancelled":true}}'
        call_api "Cancel Appointment" "PATCH" "https://nexhealth.info/appointments/$APPT_ID?subdomain=$SUBDOMAIN" "$CANCEL_PAYLOAD"
    fi
else
    echo "⚠️ Skipping booking (missing slot or patient)"
fi

echo "Done! Check $OUTPUT_FILE for details."
