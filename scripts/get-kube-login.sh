#!/bin/bash
set -e

script_dir="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

if [[ -f "$script_dir/../.env" ]]; then
	source "$script_dir/../.env"
fi

if [[ ${#RESOURCE_GROUP} -eq 0 ]]; then
  echo 'RESOURCE_GROUP not set' 1>&2
  exit 6
fi

AKS_NAME=$(jq -r '.AKS_NAME' < "$script_dir/../secrets.json")
if [[ ${#AKS_NAME} -eq 0 ]]; then
  echo 'ERROR: Missing output value aks_name' 1>&2
  exit 6
fi

echo "Getting AKS credentials"
# Get kubeconfig for the AKS cluster
az aks get-credentials --resource-group "$RESOURCE_GROUP" --name "$AKS_NAME" --overwrite-existing
# Update the kubeconfig to use  https://github.com/azure/kubelogin
# kubelogin convert-kubeconfig -l azurecli