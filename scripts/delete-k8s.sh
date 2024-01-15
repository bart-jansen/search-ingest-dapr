#!/bin/bash
set -e

script_dir="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
acr_login_server="bart001registry.azurecr.io"

echo "### Deploying components-k8s"
kubectl delete -f "$script_dir/../components-k8s"

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
    echo "### Deploying $component"
    cat "$script_dir/../src/$component/deploy.yaml" \
    | REGISTRY_NAME=$acr_login_server \
        envsubst \
    | kubectl delete -f -
done