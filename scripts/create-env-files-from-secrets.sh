#!/bin/bash
set -e

#
# This script expects to find an secrets.json in the root folder with the values
# from the infrastructure deployment.
#

script_dir="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

output_file="$script_dir/../secrets.json"

if [[ -f "$script_dir/../.env" ]]; then
	source "$script_dir/../.env"
fi

instrumentation_key=$(jq -r '.INSTRUMENTATION_KEY' < "$output_file")
cat <<EOF > "$script_dir/../components-k8s/otel.secret.yaml"
apiVersion: v1
kind: Secret
metadata:
  name: otel-collector-secrets
  labels:
    app: opentelemetry
type: Opaque
data:
  instrumentation_key: $(echo -n "$instrumentation_key" | base64 -w 0)
EOF
echo "CREATED: k8s otel secret file"

service_bus_connection_string=$(jq -r '.SERVICE_BUS_CONNECTION_STRING' < "$output_file")
cat <<EOF > "$script_dir/../components-k8s/pubsub.secret.yaml"
apiVersion: v1
kind: Secret
metadata:
  name: servicebus-pubsub-secret
  namespace: default
type: Opaque
data:
  connectionString: $(echo -n "$service_bus_connection_string" | base64 -w 0)
EOF
echo "CREATED: k8s pubsub secret file"


redis_host=$(jq -r '.REDIS_HOST' < "$output_file")
redis_password=$(jq -r '.REDIS_PASSWORD' < "$output_file")
cat <<EOF > "$script_dir/../components-k8s/statestore.secret.yaml"
apiVersion: v1
kind: Secret
metadata:
  name: redis-statestore-secret
  namespace: default
type: Opaque
data:
   redisHost: $(echo -n "$redis_host" | base64 -w 0)
   redisPassword: $(echo -n "$redis_password" | base64 -w 0)

EOF
echo "CREATED: k8s redis secret file"


OUTPUT_SECRET_FILE="$script_dir/../components-k8s/k8s.secret.yaml"

# Extract the 'secretstore' object from the input JSON file
secretstore=$(jq -r '.secretstore' "$output_file")

# Check if the 'secretstore' object was found
if [ "$secretstore" == "null" ]; then
    echo "The 'secretstore' object was not found in the secrets file."
    exit 1
fi

# Start generating the Kubernetes secret YAML
cat > "$OUTPUT_SECRET_FILE" <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: secretstore
  namespace: default
type: Opaque
data:
EOF

# Iterate over the keys in the 'secretstore' object and append them to the YAML file
for key in $(echo -n "$secretstore" | jq -r 'keys[]'); do
  echo "Processing key $key"
  # Extract the value for the current key
  value=$(echo -n "$secretstore" | jq -r --arg key "$key" '.[$key]')
  # Base64 encode the value and append the key-value pair to the YAML file
  echo "  $key: $(echo -n "$value" | base64 -w 0)" >> "$OUTPUT_SECRET_FILE"
done

echo "Kubernetes secret file $OUTPUT_SECRET_FILE has been created."