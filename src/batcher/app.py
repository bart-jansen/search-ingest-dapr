import json
from flask import Flask, request, jsonify
from azure.storage.blob import BlobServiceClient
from dapr.clients import DaprClient
from nanoid import generate
import os
import logging

logging.basicConfig(level=logging.INFO)

# Initialize Flask app and Dapr client
app = Flask(__name__)
dapr_client = DaprClient()

# Configuration
APP_PORT = os.getenv("APP_PORT", "6000")
PUBSUB_NAME = 'pubsub'
SECRET_STORE = 'secretstore'
DESTINATION_TOPIC_NAME = 'extract-document'

# Helper functions
def get_required_data(request_data, *keys):
    return (request_data.get(key) for key in keys)

def publish_event_for_blob(blob, ingestion_id, source_folder_path, destination_folder_path):
    doc_id = generate(size=8)
    filename = blob.name
    dapr_client.publish_event(
        pubsub_name=PUBSUB_NAME,
        topic_name=DESTINATION_TOPIC_NAME,
        data=json.dumps({
            'ingestion_id': ingestion_id,
            'doc_id': doc_id,
            'filename': filename.split(source_folder_path)[1],
            'source_folder_path': source_folder_path,
            'destination_folder_path': destination_folder_path
        }),
        data_content_type='application/json',
    )
    logging.info(f'Published filename ({filename}) with document ID ({doc_id})')
    return doc_id

@app.route('/batcher-trigger', methods=['POST'])
def batcher_trigger():
    logging.info('HTTP trigger received!')

    # get blob connection string
    blob_secret = dapr_client.get_secret(store_name=SECRET_STORE, key="secretstore").secret["AZURE_BLOB_CONNECTION_STRING"]
    blob_container_name = dapr_client.get_secret(store_name=SECRET_STORE, key="secretstore").secret["BLOB_CONTAINER_NAME"]

    print(f'Blob connection string: {blob_secret}', flush=True)
    print(f'Blob container name: {blob_container_name}', flush=True)

    # Extract required data from request
    request_data = request.get_json()
    if not request_data:
        return jsonify(success=False, error="Invalid JSON data"), 400

    source_folder_path, destination_folder_path, searchitems_folder_path, searchindexer_name = get_required_data(
        request_data,
        'source_folder_path',
        'destination_folder_path',
        'searchitems_folder_path',
        'searchindexer_name'
    )

    if not all([source_folder_path, destination_folder_path, searchitems_folder_path, searchindexer_name]):
        return jsonify(success=False, error="All required fields must be provided."), 400

    # Initialize Azure Blob Service Client
    blob_service_client = BlobServiceClient.from_connection_string(blob_secret)
    container_client = blob_service_client.get_container_client(blob_container_name)
    blob_list = list(container_client.list_blobs(name_starts_with=source_folder_path))

    # Generate ingestion ID
    ingestion_id = generate(size=5, alphabet='abcdefghijklmnopqrstuvwxyz0123456789')
    document_size = len(blob_list)
    doc_ids = []

    logging.info(f'Started ingestion on {source_folder_path} with Ingestion ID: {ingestion_id} with total documents: {document_size}')

    # Publish events for each blob
    for blob in blob_list:
        doc_id = publish_event_for_blob(blob, ingestion_id, source_folder_path, destination_folder_path)
        doc_ids.append(doc_id)

    # Save the state of the ingestion
    state_key = f'ingestion-{ingestion_id}'
    dapr_client.save_state(store_name='statestore', key=state_key, value=json.dumps({
        'doc_ids': doc_ids,
        'destination_folder_path': destination_folder_path,
        'searchitems_folder_path': searchitems_folder_path,
        'searchindexer_name': searchindexer_name
    }))

    return jsonify(success=True), 200

app.run(port=APP_PORT)