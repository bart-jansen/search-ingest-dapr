#!/bin/bash
set -e

script_dir="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

acr_name=$(jq -r '.ACR_NAME' < "$script_dir/../secrets.json")
if [[ ${#acr_name} -eq 0 ]]; then
  echo 'ERROR: Missing secrets value acr_name' 1>&2
  exit 6
fi

acr_login_server=$(jq -r '.ACR_LOGIN_SERVER' < "$script_dir/../secrets.json")
if [[ ${#acr_login_server} -eq 0 ]]; then
  echo 'ERROR: Missing secrets value acr_login_server' 1>&2
  exit 6
fi

# Log in to Azure Container Registry
az acr login --name "$acr_name"

# List of components to build and push
components=(
    "batcher"
    "document_completed"
    "enrichment_completed"
    "extract_document"
    "generate_embeddings"
    "generate_keyphrases"
    "generate_summaries"
    "process_document"
)

# Iterate over components and use the build_and_push function for each
for component in "${components[@]}"; do
    docker build --platform linux/amd64  -t "$acr_login_server/$component" src/$component
    docker push "$acr_login_server/$component"
done


