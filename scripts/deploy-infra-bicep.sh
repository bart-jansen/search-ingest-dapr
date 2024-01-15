#!/bin/bash
set -e
#
# This script generates the bicep parameters file and then uses that to deploy the infrastructure
# An secrets.json file is generated in the project root containing the outputs from the deployment
#

script_dir="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

if [[ -f "$script_dir/../.env" ]]; then
	source "$script_dir/../.env"
fi

if [[ -z "$RESOURCE_PREFIX" ]]; then
	echo 'RESOURCE_PREFIX not set - ensure you have specifed a value for it in your .env file' 1>&2
	exit 6
fi

az group create --name $RESOURCE_GROUP --location $LOCATION

cat << EOF > "$script_dir/../infra/azuredeploy.parameters.json"
{
  "\$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#",
  "contentVersion": "1.0.0.0",
  "parameters": {
    "resourceNamePrefix": {
      "value": "${RESOURCE_PREFIX}"
    }
  }
}
EOF

output_file="$script_dir/../secrets.json"
output_bak_file="$script_dir/../infra/output.bak"
deployment_name="deployment-${USERNAME}-${LOCATION}"
cd infra
echo "Deploying to $RESOURCE_GROUP in $LOCATION"
result=$(az deployment group create \
	--resource-group $RESOURCE_GROUP \
  --template-file main.bicep \
  --name "$deployment_name" \
  --parameters azuredeploy.parameters.json \
  --output json)

# Check if the deployment was successful before proceeding
if [[ $? -eq 0 ]]; then
    # Backup existing output file, if it exists
    if [[ -f "$output_file" ]]; then
        if [[ -f "$output_bak_file" ]]; then
            rm "$output_bak_file"
        fi
        mv "$output_file" "$output_bak_file"
    fi

    # Parse the result and create the desired JSON structure
    echo "$result" | jq '{
        SERVICE_BUS_CONNECTION_STRING: .properties.outputs.service_bus_connection.value,
        REDIS_HOST: .properties.outputs.redis_host.value,
        REDIS_PASSWORD: .properties.outputs.redis_password.value,
        AKS_NAME: .properties.outputs.aks_name.value,
        ACR_NAME: .properties.outputs.acr_name.value,
        ACR_LOGIN_SERVER: .properties.outputs.acr_login_server.value,
        INSTRUMENTATION_KEY: .properties.outputs.app_insights_instrumentation_key.value,
        secretstore: {
            AZURE_BLOB_CONNECTION_STRING: .properties.outputs.storage_connection_string.value,
            BLOB_CONTAINER_NAME: .properties.outputs.storage_container_name.value,
            FORM_RECOGNIZER_ENDPOINT: .properties.outputs.form_recognizer_endpoint.value,
            FORM_RECOGNIZER_KEY: .properties.outputs.form_recognizer_key.value,
            OPENAI_ENDPOINT: .properties.outputs.openai_service_endpoint.value,
            OPENAI_DEPLOYMENT: .properties.outputs.openai_service_deployment_name.value,
            OPENAI_KEY: .properties.outputs.openai_service_key.value,
            AZURE_LANGUAGE_ENDPOINT: .properties.outputs.cognitive_service_endpoint.value,
            AZURE_LANGUAGE_KEY: .properties.outputs.cognitive_service_key.value,
            SEARCH_SERVICE: .properties.outputs.search_name.value,
            SEARCH_KEY: .properties.outputs.search_key.value
        }
    }' > "$output_file"
else
    echo "Deployment failed"
    exit 1
fi

echo "Deployment complete, output saved to $output_file"

echo "Ensure k8s-extension extension is installed"
extension_installed=$(az extension list --query "length([?contains(name, 'k8s-extension')])")
if [[ $extension_installed -eq 0 ]]; then
  echo "Installing k8s-extension extension for az CLI"
  az extension add --name k8s-extension
fi

provider_state=$(az provider list --query "[?contains(namespace,'Microsoft.KubernetesConfiguration')] | [0].registrationState" -o tsv)
if [[ $provider_state != "Registered" ]]; then
  echo "Registering Microsoft.KubernetesConfiguration provider"
  az provider register --namespace Microsoft.KubernetesConfiguration
fi

AKS_NAME=$(jq -r '.AKS_NAME' < "$output_file")
if [[ ${#AKS_NAME} -eq 0 ]]; then
  echo 'ERROR: Missing output value AKS_NAME' 1>&2
  exit 6
fi

dapr_installed=$(az k8s-extension list --resource-group $RESOURCE_GROUP --cluster-name $AKS_NAME --cluster-type managedClusters --query "length([?name=='dapr'])" -o tsv)
if [[ "$dapr_installed" == "1" ]]; then
  echo "Dapr extension already installed"
else
  echo "Create Dapr extension for AKS cluster"
  az k8s-extension create --cluster-type managedClusters \
  --cluster-name $AKS_NAME \
  --resource-group $RESOURCE_GROUP \
  --name dapr \
  --extension-type Microsoft.Dapr
fi