import time
from tenacity import retry, wait_random_exponential, stop_after_attempt, retry_if_exception_type
from flask import Flask, request, jsonify
from cloudevents.http import from_http
from dapr.clients import DaprClient
import json
import os
from azure.core.credentials import AzureKeyCredential
from azure.ai.textanalytics import TextAnalyticsClient
from azure.core.exceptions import AzureError

dapr_client = DaprClient()
app = Flask(__name__)
app_port = os.getenv("APP_PORT", "6003")

source_topic = "generate-keyphrases"
destination_topic = "enrichment-completed"
pubsub_name = "pubsub"
secret_store = "secretstore"

@retry(retry=retry_if_exception_type(AzureError), wait=wait_random_exponential(min=15, max=60), stop=stop_after_attempt(40))
def compute_keyphrases(texts, batch_nr):
    language_endpoint = dapr_client.get_secret(store_name=secret_store, key="secretstore").secret["AZURE_LANGUAGE_ENDPOINT"]
    language_key = dapr_client.get_secret(store_name=secret_store, key="secretstore").secret["AZURE_LANGUAGE_KEY"]
    text_analytics_client = TextAnalyticsClient(endpoint=language_endpoint, credential=AzureKeyCredential(language_key))

    result = text_analytics_client.extract_key_phrases(texts)
    if result and len(result) == len(texts) and not any([resp.is_error for resp in result]):
        return [data.key_phrases for data in result]
    else:
        error_message = f"Error occurred in keyphrases batch_nr: {batch_nr}"
        print(error_message, flush=True)
        raise AzureError(error_message)  

@app.route("/dapr/subscribe", methods=["GET"])
def subscribe():
    subscriptions = [
        {"pubsubname": pubsub_name, "topic": source_topic, "route": "generate-keyphrases"}
    ]
    print("Dapr pub/sub is subscribed to: " + json.dumps(subscriptions), flush=True)
    return jsonify(subscriptions)

@app.route("/generate-keyphrases", methods=["POST"])
def generate_keyphrases_subscriber():
    event = from_http(request.headers, request.get_data())
    data = json.loads(event.data)

    ingestion_id = data["ingestion_id"]
    doc_id = data["doc_id"]
    batch_key = data["batch_key"]
    batch_nr = data["batch_nr"]
    total_batch_size = data["total_batch_size"]

    # print(f"Received form recognizer statestore reference: {batch_key} with document ID: {doc_id}", flush=True)

    try:
        # Retrieve the Form Recognizer result from Redis using Dapr state store
        state_item = dapr_client.get_state(store_name="statestore", key=batch_key)
        batch_result = json.loads(state_item.data) if state_item.data else None
        keyphrases = compute_keyphrases([section["content"] for section in batch_result], batch_nr)

        # show the keyphrases
        # print(f"Keyphrases extracted: {json.dumps(keyphrases)}", flush=True)
        
        # Store the keyphrases result in Redis
        keyphrases_result_key = f"keyphrases-output-{doc_id}-batch-{batch_nr}"
        dapr_client.save_state(store_name="statestore", key=keyphrases_result_key, value=json.dumps(keyphrases))
        # print(f"Stored keyphrases result with key: {keyphrases_result_key}", flush=True)

        # Publish the completion event to the enrichment-completed topic
        dapr_client.publish_event(
            pubsub_name=pubsub_name,
            topic_name=destination_topic,
            data=json.dumps({
                "ingestion_id": ingestion_id,
                "doc_id": doc_id, 
                "service_name": "generate-keyphrases", 
                "result_key": keyphrases_result_key,
                "batch_nr": batch_nr,
                "total_batch_size": total_batch_size
            }),
        )
        print(f"Published completion event for keyphrases with document ID: {doc_id}", flush=True)

    except Exception as e:
        print(f"An error occurred: {str(e)}", flush=True)
        return json.dumps({"success": False, "error": str(e)}), 500, {"ContentType": "application/json"}

    return json.dumps({"success": True}), 200, {"ContentType": "application/json"}

app.run(port=app_port)
