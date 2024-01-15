from flask import Flask, request, jsonify
from cloudevents.http import from_http
from dapr.clients import DaprClient
import json
import os
import openai
import time
from tenacity import retry, wait_random_exponential, stop_after_attempt, retry_if_exception_type

dapr_client = DaprClient()
app = Flask(__name__)
app_port = os.getenv("APP_PORT", "6002")

source_topic = "generate-embeddings"
destination_topic = "messages"
pubsub_name = "pubsub"
secret_store = "secretstore"

# OpenAI setup
openai.api_type = "azure"
openai.api_version = "2023-05-15"

CACHE_KEY_TOKEN_TYPE = "token_type"  # Define the missing constant
open_ai_token_cache = {}  # Define the missing variable
CACHE_KEY_TOKEN_CRED = "token_cred"  # Define the missing constant
CACHE_KEY_CREATED_TIME = "created_time"  # Define the missing constant

def refresh_openai_token():
    """
    Refresh OpenAI token every 5 minutes
    """
    if openai.api_type == 'azure_ad' and CACHE_KEY_TOKEN_TYPE in open_ai_token_cache and open_ai_token_cache[CACHE_KEY_TOKEN_TYPE] == 'azure_ad' and open_ai_token_cache[CACHE_KEY_CREATED_TIME] + 300 < time.time():
        token_cred = open_ai_token_cache[CACHE_KEY_TOKEN_CRED]
        openai.api_key = token_cred.get_token("https://cognitiveservices.azure.com/.default").token
        open_ai_token_cache[CACHE_KEY_CREATED_TIME] = time.time()

@retry(retry=retry_if_exception_type(openai.error.RateLimitError), wait=wait_random_exponential(min=15, max=60), stop=stop_after_attempt(30))
def compute_embedding_in_batch(texts):
    refresh_openai_token()
    try:
        OPENAI_ENDPOINT = dapr_client.get_secret(store_name=secret_store, key="secretstore").secret["OPENAI_ENDPOINT"]
        OPENAI_DEPLOYMENT = dapr_client.get_secret(store_name=secret_store, key="secretstore").secret["OPENAI_DEPLOYMENT"]
        OPENAI_KEY = dapr_client.get_secret(store_name=secret_store, key="secretstore").secret["OPENAI_KEY"]
        openai.api_key = OPENAI_KEY
        openai.api_base = OPENAI_ENDPOINT
        emb_response = openai.Embedding.create(engine=OPENAI_DEPLOYMENT, input=texts)
        
        if not emb_response["data"][0]["embedding"]:
            raise ValueError("Empty embedding returned")
        return [data.embedding for data in emb_response.data]
    except openai.error.OpenAIError as e:
        print(f"OpenAI API error: {e}", flush=True)
        raise  # Reraise the exception to trigger the retry mechanism

@app.route("/dapr/subscribe", methods=["GET"])
def subscribe():
    subscriptions = [
        {"pubsubname": pubsub_name, "topic": source_topic, "route": "generate-embeddings"}
    ]
    print("Dapr pub/sub is subscribed to: " + json.dumps(subscriptions), flush=True)
    return jsonify(subscriptions)

@app.route("/generate-embeddings", methods=["POST"])
def generate_embeddings_subscriber():
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

        if batch_result is None:
            raise ValueError("No section result found for the provided result key")

        embeddings = compute_embedding_in_batch([section["content"] for section in batch_result])

        # Store the embedding result in Redis
        embedding_result_key = f"embedding-output-{doc_id}-batch-{batch_nr}"
        dapr_client.save_state(store_name="statestore", key=embedding_result_key, value=json.dumps(embeddings))
        # print(f"Stored embedding result with key: {embedding_result_key}", flush=True)

        # Publish the completion event to the enrichment-completed topic
        dapr_client.publish_event(
            pubsub_name=pubsub_name,
            topic_name="enrichment-completed",
            data=json.dumps({
                "ingestion_id": ingestion_id,
                "doc_id": doc_id, 
                "service_name": "generate-embeddings", 
                "result_key": embedding_result_key,
                "batch_nr": batch_nr,
                "total_batch_size": total_batch_size
            }),
        )
        print(f"Published completion event for embeddings with document ID: {doc_id}", flush=True)

    except Exception as e:
        print(f"An error occurred: {str(e)}", flush=True)
        return json.dumps({"success": False, "error": str(e)}), 500, {"ContentType": "application/json"}

    return json.dumps({"success": True}), 200, {"ContentType": "application/json"}


app.run(port=app_port)
