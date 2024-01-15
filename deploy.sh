#!/bin/bash
set -e

script_dir="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

if [[ -f "$script_dir/.env" ]]; then
	source "$script_dir/.env"
fi

export LOCATION=${LOCATION:-westeurope}

echo "### Deploying infrastructure"
$script_dir/scripts/deploy-infra-bicep.sh

echo "### Building and pushing Docker images"
$script_dir/scripts/docker-build-and-push.sh

echo "### Generating manifests"
$script_dir/scripts/create-env-files-from-output.sh

echo "### Getting kubectl credentials"
$script_dir/scripts/get-kube-login.sh

echo "### Deploying components/services to Kubernetes"
$script_dir/scripts/deploy-to-k8s.sh