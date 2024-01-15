import time
import json
import os
from azure_search_index import AzureSearchIndex
from tenacity import retry, stop_after_attempt, wait_random_exponential, retry_if_exception_type, before_sleep_log
from flask import Flask, request, jsonify
from azure.storage.blob import BlobServiceClient
from cloudevents.http import from_http
from dapr.clients import DaprClient, DaprInternalError


dapr_client = DaprClient()
app = Flask(__name__)
app_port = os.getenv("APP_PORT", "6007")

source_topic = "document-completed"
pubsub_name = "pubsub"
store_name = "statestore"
secret_store = "secretstore"

azure_blob_connection_string = dapr_client.get_secret(store_name=secret_store, key="secretstore").secret["AZURE_BLOB_CONNECTION_STRING"]
blob_container_name = dapr_client.get_secret(store_name=secret_store, key="secretstore").secret["BLOB_CONTAINER_NAME"]
search_service = dapr_client.get_secret(store_name=secret_store, key="secretstore").secret["SEARCH_SERVICE"]
search_key = dapr_client.get_secret(store_name=secret_store, key="secretstore").secret["SEARCH_KEY"]

# update state with transactions/etag to avoid conflicts
@retry(stop=stop_after_attempt(3), wait=wait_random_exponential(multiplier=1, max=10),
            retry=retry_if_exception_type(DaprInternalError))
def save_state_with_retry(state_key, ingestion_data, etag):
    dapr_client.save_state(store_name=store_name, key=state_key, value=json.dumps(ingestion_data), etag=etag)


def delete_blobs_with_prefix(container_client, prefix):
    blob_list = container_client.list_blobs(name_starts_with=prefix)
    blob_count = 0
    for blob in blob_list:
        try:
            blob_count += 1
            container_client.delete_blob(blob.name)
        except Exception as e:
            print(f"Error deleting blob {blob.name}: {e}")
    
    print(f"🗑️ Deleted {blob_count} blobs with prefix {prefix}", flush=True)


def cleanup_blob(status, blob_container_name, searchitems_folder_path):
    print("Indexing completed with status:", status.last_result.status)

    blob_service_client = BlobServiceClient.from_connection_string(azure_blob_connection_string)
    container_client = blob_service_client.get_container_client(blob_container_name)
    
    print("Indexer completed. Deleting all blobs now..", flush=True)
    delete_blobs_with_prefix(container_client, searchitems_folder_path)

    print("🏁🏁🏁Successfully indexed and cleaned up.", flush=True)

def start_indexer(blob_container_name, searchitems_folder_path, searchindexer_name):
    # Create an instance of AzureSearchIndex
    azure_search_index = AzureSearchIndex(
        service_name = search_service, 
        search_key = search_key, 
        blob_connection_string = azure_blob_connection_string, 
        blob_container = blob_container_name, 
        blob_items_folder = searchitems_folder_path
    )

    # Call the methods to create the datasource, index, and indexer
    azure_search_index.create_datasource(f"{searchindexer_name}-ds")
    azure_search_index.create_index(f"{searchindexer_name}-index")
    azure_search_index.create_indexer(searchindexer_name, f"{searchindexer_name}-ds", f"{searchindexer_name}-index")

    def cleanup_blob_wrapper(status):
        cleanup_blob(status, blob_container_name, searchitems_folder_path)

    azure_search_index.run_indexer(searchindexer_name, cleanup_blob_wrapper)

# This route subscribes to the pub/sub topic
@app.route("/dapr/subscribe", methods=["GET"])
def subscribe():
    subscriptions = [
        {"pubsubname": pubsub_name, "topic": source_topic, "route": "/document-completed"}
    ]
    print("Dapr pub/sub is subscribed to: " + json.dumps(subscriptions), flush=True)
    return jsonify(subscriptions)

# This route is triggered when a service publishes a message to the topic
@app.route("/document-completed", methods=["POST"])
def document_completed_subscriber():
    event = from_http(request.headers, request.get_data())
    data = json.loads(event.data)

    ingestion_id = data["ingestion_id"]
    doc_id = data["doc_id"]

    # get ingestion size from state store
    state_key = f'ingestion-{ingestion_id}'
    state_item = dapr_client.get_state(store_name=store_name, key=state_key)

    # if state item does not exist, then we are done
    if not state_item.data:
        print(f"Fully processed document: {doc_id}, total remaining documents 0", flush=True)
        return json.dumps({"success": True}), 200, {"ContentType": "application/json"}
    
    ingestion_data = json.loads(state_item.data)

    # remove the doc_id from the list, only if it exists
    if doc_id in ingestion_data['doc_ids']:
        ingestion_data['doc_ids'].remove(doc_id)
        try:
            save_state_with_retry(state_key, ingestion_data, state_item.etag)
        except DaprInternalError as e:
            # logging.error(f"Failed to save state: {e}")
            return json.dumps({"success": False, "error": str(e)}), 500, {"ContentType": "application/json"}
    
    document_size = len(ingestion_data['doc_ids'])

    # if document size is 0, then we are done
    if document_size == 0:
        print(f"🏁Fully processed document: {doc_id}, total remaining documents {document_size}", flush=True)
        # start indexer
        start_indexer(
            blob_container_name, 
            ingestion_data['searchitems_folder_path'], 
            ingestion_data['searchindexer_name']
        )

    else :
        print(f"Total remaining documents {document_size}", flush=True)

    return json.dumps({"success": True}), 200, {"ContentType": "application/json"}


app.run(port=app_port)