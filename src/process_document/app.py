import math
from flask import Flask, request, jsonify
from cloudevents.http import from_http
from dapr.clients import DaprClient
import json
import os
from azure.storage.blob import BlobServiceClient
from document_chunker import create_sections, process_with_form_recognizer

dapr_client = DaprClient()
app = Flask(__name__)
app_port = os.getenv("APP_PORT", "6001")

source_topic = "process-document"
secret_store = "secretstore"
pubsub_name = "pubsub"

BATCH_SIZE = 8

def save_and_publish_batch(ingestion_id, doc_id, batch_nr, batch_content, total_batch_size):
    batch_key = f"section-output-{doc_id}-batch-{batch_nr}"
    dapr_client.save_state(store_name="statestore", key=batch_key, value=json.dumps(batch_content))

    # Publish events for the batch
    for topic in ["generate-embeddings", "generate-keyphrases", "generate-summaries"]:
        dapr_client.publish_event(
            pubsub_name=pubsub_name,
            topic_name=topic,
            data=json.dumps({
                "ingestion_id": ingestion_id,
                "doc_id": doc_id,
                "batch_key": batch_key,
                "batch_nr": batch_nr,
                "total_batch_size": total_batch_size
            }),
        )

@app.route("/dapr/subscribe", methods=["GET"])
def subscribe():
    subscriptions = [
        {"pubsubname": pubsub_name, "topic": source_topic, "route": "process-document"}
    ]
    print("Dapr pub/sub is subscribed to: " + json.dumps(subscriptions), flush=True)
    return jsonify(subscriptions)

@app.route("/process-document", methods=["POST"])
def process_page_subscriber():
    event = from_http(request.headers, request.get_data())

    ingestion_id = event.data["ingestion_id"]
    doc_id = event.data["doc_id"]
    blob_name = event.data["blob_name"]
    
    print(f"Received filename: {blob_name} with document ID: {doc_id}", flush=True)

    try:
        # Get the blob client for the specific blob
        blob_secret = dapr_client.get_secret(store_name=secret_store, key="secretstore").secret["AZURE_BLOB_CONNECTION_STRING"]
        blob_container_name = dapr_client.get_secret(store_name=secret_store, key="secretstore").secret["BLOB_CONTAINER_NAME"]
        blob_service_client = BlobServiceClient.from_connection_string(blob_secret)
        blob_client = blob_service_client.get_blob_client(container=blob_container_name, blob=blob_name)
        
        # Download the blob content
        blob_content = blob_client.download_blob().readall()

        print(f"Successfully downloaded blob for analyzing: {blob_name} with document ID: {doc_id}", flush=True)

        # Process the page with Azure Form Recognizer
        fr_endpoint = dapr_client.get_secret(store_name=secret_store, key="secretstore").secret["FORM_RECOGNIZER_ENDPOINT"]
        fr_key = dapr_client.get_secret(store_name=secret_store, key="secretstore").secret["FORM_RECOGNIZER_KEY"]

        form_recognizer_result = process_with_form_recognizer(blob_content, fr_endpoint, fr_key)

        # print byte size and amount of characters of form_recognizer_result
        print(f"form_recognizer_result byte size: {len(json.dumps(form_recognizer_result))}", flush=True)

        sections = list(create_sections(blob_name.split('/')[-1], form_recognizer_result, doc_id, ingestion_id))

        # print section size of list
        print(f"entire sections size: {len(sections)}", flush=True)

        batch_nr = 1  # Initialize batch number
        batch_content = []
        total_batch_size = math.ceil(len(sections) / BATCH_SIZE)

        print(f"total batch size: {total_batch_size}", flush=True)

        ## loop through sections, append to batch_content, and save content in Redis and publish event for each full batch
        for section in sections:
            batch_content.append(section)
            if len(batch_content) == BATCH_SIZE:  # Check if the batch size is reached
                save_and_publish_batch(ingestion_id, doc_id, batch_nr, batch_content, total_batch_size)
                # Reset the batch content and increment the batch number
                batch_content = []
                batch_nr += 1

        # Check if there are any sections left in the batch_content after the loop
        if batch_content:
            save_and_publish_batch(ingestion_id, doc_id, batch_nr, batch_content, total_batch_size)
            

    except Exception as e:
        print(f"An error occurred while downloading the blob: {e}", flush=True)
        return json.dumps({"success": False, "error": str(e)}), 500
    
    return json.dumps({"success": True}), 200, {"ContentType": "application/json"}


app.run(port=app_port)
