from tenacity import retry, stop_after_attempt, wait_random_exponential, retry_if_exception_type, before_sleep_log
from flask import Flask, request, jsonify
from azure.storage.blob import BlobServiceClient
from cloudevents.http import from_http
from dapr.clients import DaprClient
import json
import os

dapr_client = DaprClient()
app = Flask(__name__)
app_port = os.getenv("APP_PORT", "6006")

source_topic = "enrichment-completed"
pubsub_name = "pubsub"
destination_topic = "document-completed"
store_name = "statestore"  
secret_store = "secretstore"

# This route subscribes to the pub/sub topic
@app.route("/dapr/subscribe", methods=["GET"])
def subscribe():
    subscriptions = [
        {"pubsubname": pubsub_name, "topic": source_topic, "route": "/enrichment-completed"}
    ]
    print("Dapr pub/sub is subscribed to: " + json.dumps(subscriptions), flush=True)
    return jsonify(subscriptions)

# This route is triggered when a service publishes a message to the topic
@app.route("/enrichment-completed", methods=["POST"])
def enrichment_completed_subscriber():
    event = from_http(request.headers, request.get_data())
    data = json.loads(event.data)

    ingestion_id = data["ingestion_id"]
    doc_id = data["doc_id"]
    service_name = data["service_name"]
    result_key = data["result_key"]
    batch_nr = data["batch_nr"]
    total_batch_size = data["total_batch_size"]
    # print(f"Received {service_name} statestore reference: {result_key} with document ID: {doc_id}", flush=True)

    ingestion_response = dapr_client.get_state(store_name=store_name, key=f"ingestion-{ingestion_id}").data
    embeddings_response = dapr_client.get_state(store_name=store_name, key=f"embedding-output-{doc_id}-batch-{batch_nr}").data
    keyphrases_response = dapr_client.get_state(store_name=store_name, key=f"keyphrases-output-{doc_id}-batch-{batch_nr}").data
    summaries_response = dapr_client.get_state(store_name=store_name, key=f"summaries-output-{doc_id}-batch-{batch_nr}").data
    sections_response = dapr_client.get_state(store_name=store_name, key=f"section-output-{doc_id}-batch-{batch_nr}").data

    # check if both embeddings response and keyphrases exist, otherwise return
    if not ingestion_response or not embeddings_response or not keyphrases_response or not summaries_response or not sections_response:
        # print(f"Missing embeddings, keyphrases, summaries or sections for {doc_id}-batch-{batch_nr}", flush=True)
        return json.dumps({"success": True}), 200, {"ContentType": "application/json"}

    try:
        ingestion = json.loads(ingestion_response)
        embeddings = json.loads(embeddings_response)
        keyphrases = json.loads(keyphrases_response)
        summaries = json.loads(summaries_response)
        sections = json.loads(sections_response)
    except json.decoder.JSONDecodeError as e:
        print(f"Error decoding embeddings: {e}", flush=True)
        return json.dumps({"success": False}), 500, {"ContentType": "application/json"}
    except Exception as e:
        print(f"Error occurred: {e}", flush=True)
        return json.dumps({"success": False}), 500, {"ContentType": "application/json"}
    
    ## check if the number of embeddings and keyphrases match
    if sections is None or keyphrases is None or embeddings is None or summaries is None or len(embeddings) != len(keyphrases) or len(sections) != len(keyphrases) or len(summaries) != len(keyphrases):
        print(f"Number of embeddings and keyphrases do not match for {doc_id}-batch-{batch_nr}", flush=True)
        print(f"Number of embeddings: {len(embeddings)}", flush=True)
        print(f"Number of keyphrases: {len(keyphrases)}", flush=True)
        print(f"Number of summaries: {len(summaries)}", flush=True)
        print(f"Number of sections: {len(sections)}", flush=True)
        return json.dumps({"success": False}), 500, {"ContentType": "application/json"}

    ## append embeddings and keyphrases in sections
    for (i, section) in enumerate(sections):
        section['embeddings'] = embeddings[i]
        section['keyphrases'] = keyphrases[i]
        section['summaries'] = summaries[i]

    # print(f"Ingestion data: {ingestion}", flush=True)

    ## initialize blob
    azure_blob_connection_string = dapr_client.get_secret(store_name=secret_store, key="secretstore").secret["AZURE_BLOB_CONNECTION_STRING"]
    blob_container_name = dapr_client.get_secret(store_name=secret_store, key="secretstore").secret["BLOB_CONTAINER_NAME"]
    blob_service_client = BlobServiceClient.from_connection_string(azure_blob_connection_string)
    container_client = blob_service_client.get_container_client(blob_container_name)

    ## upload json to blob storage
    blob_name = f"{ingestion['searchitems_folder_path']}{doc_id}-batch-{batch_nr}.json"
    uploaded_blob_client = container_client.get_blob_client(blob=blob_name)
    
    ## convert sections to json and upload to blob
    uploaded_blob_client.upload_blob(json.dumps(sections), overwrite=True)

    ## check in blob storage how many other blobs are uploaded with wildcard for the section number:
    blob_path = f"{ingestion['searchitems_folder_path']}{doc_id}-batch-"
    blob_list = container_client.list_blobs(name_starts_with=blob_path)
    blob_count = len(list(blob_list))

    check_section_completion(ingestion_id, doc_id, blob_count, total_batch_size)

    ## delete the keys from redis
    dapr_client.delete_state(store_name=store_name, key=f"embedding-output-{doc_id}-batch-{batch_nr}")
    dapr_client.delete_state(store_name=store_name, key=f"keyphrases-output-{doc_id}-batch-{batch_nr}")
    dapr_client.delete_state(store_name=store_name, key=f"summaries-output-{doc_id}-batch-{batch_nr}")
    dapr_client.delete_state(store_name=store_name, key=f"section-output-{doc_id}-batch-{batch_nr}")

    return json.dumps({"success": True}), 200, {"ContentType": "application/json"}

def check_section_completion(ingestion_id, doc_id, blob_count, section_length):
    # Check if all sections are completed
    if section_length and blob_count == int(section_length):
        print(f"✅✅✅ Document fully processed with document ID: {doc_id}", flush=True)
        
        dapr_client.publish_event(
            pubsub_name=pubsub_name,
            topic_name=destination_topic,
            data=json.dumps({
                "ingestion_id": ingestion_id,
                "doc_id": doc_id
            })
        )
    else:
        print(f"Completed sections: {blob_count} of {section_length}", flush=True)

app.run(port=app_port)