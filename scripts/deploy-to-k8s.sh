#!/bin/bash
set -e

script_dir="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
acr_login_server=$(jq -r '.ACR_LOGIN_SERVER' < "$script_dir/../secrets.json")
if [[ ${#acr_login_server} -eq 0 ]]; then
  echo 'ERROR: Missing secrets value acr_login_server' 1>&2
  exit 6
fi

echo "### Deploying components-k8s"
kubectl apply -f "$script_dir/../components-k8s"


# List of components to build and push
components=(
    "batcher"
    "document_completed"
    "enrichment_completed"
    "generate_embeddings"
    "generate_keyphrases"
    "generate_summaries"
    "process_document"
)

# Iterate over components and use the build_and_push function for each
for component in "${components[@]}"; do
    echo "### Deploying $component"
    cat "$script_dir/../src/$component/deploy.yaml" \
    | REGISTRY_NAME=$acr_login_server \
        envsubst \
    | kubectl apply -f -
done