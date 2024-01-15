from flask import Flask, request, jsonify
from cloudevents.http import from_http
from dapr.clients import DaprClient
import json
import os
from azure.storage.blob import BlobServiceClient

dapr_client = DaprClient()
app = Flask(__name__)
app_port = os.getenv("APP_PORT", "6001")

source_topic = "extract-document"
destination_topic = "process-document"
secret_store = 'secretstore'
pubsub_name = "pubsub"

@app.route("/dapr/subscribe", methods=["GET"])
def subscribe():
    subscriptions = [
        {"pubsubname": pubsub_name, "topic": source_topic, "route": "extract-document"}
    ]
    print("Dapr pub/sub is subscribed to: " + json.dumps(subscriptions), flush=True)
    return jsonify(subscriptions)

@app.route("/extract-document", methods=["POST"])
def extract_document_subscriber():
    event = from_http(request.headers, request.get_data())

    ingestion_id = event.data["ingestion_id"]
    doc_id = event.data["doc_id"]
    filename = event.data["filename"]
    source_folder_path = event.data["source_folder_path"]
    destination_folder_path = event.data["destination_folder_path"]

    print(f"Received filename: {filename} with document ID: {doc_id} on folder_path {source_folder_path}", flush=True)

    blob_secret = dapr_client.get_secret(store_name=secret_store, key="secretstore").secret["AZURE_BLOB_CONNECTION_STRING"]
    blob_container_name = dapr_client.get_secret(store_name=secret_store, key="secretstore").secret["BLOB_CONTAINER_NAME"]
    blob_service_client = BlobServiceClient.from_connection_string(blob_secret)
    container_client = blob_service_client.get_container_client(blob_container_name)
    blob_client = container_client.get_blob_client(blob=source_folder_path + filename)
    blob_content = blob_client.download_blob().readall()

    # Upload the entire PDF as a single blob
    blob_name = f"{destination_folder_path}{filename}"
    uploaded_blob_client = container_client.get_blob_client(blob=blob_name)
    uploaded_blob_client.upload_blob(blob_content, overwrite=True)

    # Publish an event for the combined PDF
    dapr_client.publish_event(
        pubsub_name=pubsub_name,
        topic_name=destination_topic,
        data=json.dumps({
            "ingestion_id": ingestion_id,
            "doc_id": doc_id, 
            "blob_name": blob_name
        })
    )

    print(f"Uploaded PDF {filename} to {blob_name}", flush=True)

    return json.dumps({"success": True}), 200, {"ContentType": "application/json"}

app.run(port=app_port)