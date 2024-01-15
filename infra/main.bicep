param location string = resourceGroup().location

@description('Resource name prefix')
param resourceNamePrefix string
var envResourceNamePrefix = toLower(resourceNamePrefix)

@description('Disk size (in GB) to provision for each of the agent pool nodes. Specifying 0 will apply the default disk size for that agentVMSize')
@minValue(0)
@maxValue(1023)
param aksDiskSizeGB int = 30

@description('The number of nodes for the AKS cluster')
@minValue(1)
@maxValue(50)
param aksNodeCount int = 2

@description('The size of the Virtual Machine nodes in the AKS cluster')
param aksVMSize string = 'Standard_B2s'
// param aksVMSize string = 'Standard_D2s_v3'

@description('The Service Bus SKU to use')
param serviceBusSku string = 'Standard'

@description('The name of the Blob Storage account Container')
param blobStorageContainerName string = 'content'

/////////////////////////////////////
// Container registry
//

resource containerRegistry 'Microsoft.ContainerRegistry/registries@2022-02-01-preview' = {
  name: '${envResourceNamePrefix}registry'
  location: location
  sku: {
    name: 'Standard'
  }
}

/////////////////////////////////////
// AKS Cluster
//

resource aks 'Microsoft.ContainerService/managedClusters@2023-03-02-preview' = {
  name: '${envResourceNamePrefix}cluster'
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    dnsPrefix: 'aks'
    agentPoolProfiles: [
      {
        name: 'agentpool'
        osDiskSizeGB: aksDiskSizeGB
        count: aksNodeCount
        minCount: 1
        maxCount: aksNodeCount
        vmSize: aksVMSize
        osType: 'Linux'
        mode: 'System'
        enableAutoScaling: true
      }
    ]
    oidcIssuerProfile: {
      enabled: true
    }
    securityProfile: {
      workloadIdentity: {
        enabled: true
      }
    }
  }
}

// Enable cluster to pull images from container registry
var roleAcrPullName = 'b24988ac-6180-42a0-ab88-20f7382dd24c'
resource contributorRoleDefinition 'Microsoft.Authorization/roleDefinitions@2018-01-01-preview' existing = {
  scope: subscription()
  name: roleAcrPullName

}
resource assignAcrPullToAks 'Microsoft.Authorization/roleAssignments@2020-04-01-preview' = {
  name: guid(resourceGroup().id, containerRegistry.name, aks.name, 'AssignAcrPullToAks')
  scope: containerRegistry
  properties: {
    description: 'Assign AcrPull role to AKS'
    principalId: aks.properties.identityProfile.kubeletidentity.objectId
    principalType: 'ServicePrincipal'
    roleDefinitionId: contributorRoleDefinition.id
  }
}

/////////////////////////////////////
// Storage account
//

resource storageAccount 'Microsoft.Storage/storageAccounts@2022-05-01' = {
  name: '${envResourceNamePrefix}storage'
  location: location
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    allowBlobPublicAccess: true
    allowSharedKeyAccess: true
    publicNetworkAccess: 'Enabled'
  }

  resource blobServices 'blobServices' = {
    name: 'default'
    properties: {}
    
    resource container 'containers' =  {
      name: blobStorageContainerName
      properties: {}
    }
  }
}


/////////////////////////////////////
// Service bus namespace
//

resource serviceBus 'Microsoft.ServiceBus/namespaces@2021-11-01' = {
  name: '${envResourceNamePrefix}sb'
  location: location
  sku: {
    name: serviceBusSku
  }
  properties: {}
}

/////////////////////////////////////
// Azure AI Search
//

resource search 'Microsoft.Search/searchServices@2021-04-01-preview' = {
  name: '${envResourceNamePrefix}search'
  location: location
  properties: {}
  sku: {
    name: 'standard'
  }
}

/////////////////////////////////////
// Azure Redis Cache
//
resource redisCache 'Microsoft.Cache/redis@2022-06-01' = {

  name: '${envResourceNamePrefix}redis'
  location: location
  properties: {
    enableNonSslPort: true
    sku: {
      name: 'Basic'
      family: 'C'
      capacity: 1
    }
  }
}

/////////////////////////////////////
// Azure Form Recognizer
//

resource formRecognizer 'Microsoft.CognitiveServices/accounts@2021-10-01' = {
  name: '${envResourceNamePrefix}formrecognizer'
  location: location
  sku: {
    name: 'S0'
  }
  kind: 'FormRecognizer'
  properties: {}
}

/////////////////////////////////////
// Azure OpenAI Service
//

resource azureOpenAIService 'Microsoft.CognitiveServices/accounts@2021-10-01' = {
  name: '${envResourceNamePrefix}aoai'
  location: location
  sku: {
    name: 'S0'
  }
  kind: 'OpenAI'
  properties: {}
}

resource deployment 'Microsoft.CognitiveServices/accounts/deployments@2023-05-01' = {
  parent: azureOpenAIService
  name: 'text-embedding-ada-002'
  properties: {
    model: {
      format: 'OpenAI'
      name: 'text-embedding-ada-002'
      version: '2'
    }
  }
  sku: {
    name: 'Standard'
    capacity: 30
  }
}

/////////////////////////////////////
// Azure Cognitive Services (Text Analytics)
//

resource cognitiveService 'Microsoft.CognitiveServices/accounts@2021-10-01' = {
  name: '${envResourceNamePrefix}enrich'
  location: location
  sku: {
    name: 'S'
  }
  kind: 'TextAnalytics'
  properties: {}
}

/////////////////////////////////////
// Application Insights
//

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: '${envResourceNamePrefix}ai'
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
  }
}


/////////////////////////////////////
// Outputs
//
output acr_name string = containerRegistry.name
output acr_login_server string = containerRegistry.properties.loginServer

output aks_name string = aks.name

output storage_name string = storageAccount.name
output storage_connection_string string = 'DefaultEndpointsProtocol=https;AccountName=${storageAccount.name};AccountKey=${storageAccount.listKeys().keys[0].value};EndpointSuffix=${environment().suffixes.storage}'
output storage_container_name string = blobStorageContainerName

output service_bus_connection string = listKeys('${serviceBus.id}/AuthorizationRules/RootManageSharedAccessKey', serviceBus.apiVersion).primaryConnectionString

output redis_host string = '${redisCache.properties.hostName}:${redisCache.properties.port}'
output redis_password string = redisCache.listKeys().primaryKey

output search_endpoint string = 'https://${search.name}.search.windows.net/'
output search_name string = search.name
output search_key string = search.listAdminKeys().primaryKey

output form_recognizer_name string = formRecognizer.name
output form_recognizer_endpoint string = formRecognizer.properties.endpoint
output form_recognizer_key string = formRecognizer.listKeys().key1

output cognitive_service_name string = cognitiveService.name
output cognitive_service_endpoint string = cognitiveService.properties.endpoint
output cognitive_service_key string = cognitiveService.listKeys().key1

output openai_service_name string = azureOpenAIService.name
output openai_service_endpoint string = azureOpenAIService.properties.endpoint
output openai_service_key string = azureOpenAIService.listKeys().key1
output openai_service_deployment_name string = deployment.name

output app_insights_name string = appInsights.name
output app_insights_instrumentation_key string = appInsights.properties.InstrumentationKey
