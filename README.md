# search-ingest-dapr

End-to-end search ingestion pipeline that takes PDF documents from a folder in blob, uses form recognizer, creates embeddings & enrichments and ultimately adds these documents to an azure search index.

## Overview

![Dapr overview](dapr-search-overview.drawio.svg)

## Deploy resources

To deploy all resources, copy `sample.env` to `.env` and update the values. Then run  `./deploy.sh`.

The included `./infra/main.bicep` deploys:
- AKS cluster
- Azure Container Registry
- Storage account
- Service Bus
- Azure AI Search
- Azure Redis Cache
- Document Intelligence (formerly Form Recognizer)
- Azure OpenAI Service with Embedding model
- Azure AI Language - TextAnalytics
- Application Insights 

As part of the deployment script, `./scripts/create-env-files-from-secrets.sh` populates the infra output as secrets for your local development (in ``./secrets.json``) and product environment in `./components-k8s/*.secret.yaml`. These secrets are automatically added to the Dapr components and applications.

> Before you start, upload some PDFs in your blob storage with the specified `source_folder_path` e.g. in `/content/PDFs/` when using the path above

## Running the project locally

The easiest way to run the project is to load it in Visual Studio Code using the Dev Containers extension as this automatically sets up the environment for you.

Once open as a dev container:
1. Run `dapr init` to initialize Dapr
2. Run `dapr run -f ./dapr.yaml` to start the services
3. Invoke an HTTP trigger to the `batcher` using: 
   
```bash
curl -X POST http://127.0.0.1:6000/batcher-trigger \
     -H "Content-Type: application/json" \
     -d '{"source_folder_path": "PDFs/", "searchitems_folder_path": "searchIndexItems/", "searchindexer_name": "daprdemotest"}'
```

With the following ingestion parameters:
- `source_folder_path` - the folder in above container to source PDFs from 
- `searchitems_folder_path` - the path in above container where SearchIndexItems are stored, as configured in your Azure AI Search DataSource
- `searchindexer_name` - the name of the search indexer to use. This will be created if it doesn't exist yet

> To access the batcher in the kubernetes cluster, port-forward port 6000 using: `kubectl port-forward batcher-podid 6000:6000`

## Cleaning up Dapr logs

When running, logs for each service are written to a `.dapr` folder under each service folder.
These can be cleaned up by running `find . -name ".dapr" | xargs rm -rf` from the root of the project.

## Delete k8s deployment

When you're switching around between local development and production, you might want to temporarily delete your k8s deployment since these pods are described to the same pubsub, picking up new messages.

To delete your deployment, run `./scripts/delete-k8s.sh`. Once you're ready testing, simply run `./scripts/deploy-to-k8s.sh` again to deploy to your k8s cluster.
