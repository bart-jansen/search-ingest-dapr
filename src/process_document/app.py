import math
import time
from flask import Flask, request, jsonify
from cloudevents.http import from_http
from dapr.clients import DaprClient
import json
import os
import html
from azure.storage.blob import BlobServiceClient
from pypdf import PdfReader, PdfWriter
from azure.ai.formrecognizer import DocumentAnalysisClient
from azure.core.credentials import AzureKeyCredential

dapr_client = DaprClient()
app = Flask(__name__)
app_port = os.getenv("APP_PORT", "6002")

source_topic = "process-document"
secret_store = "secretstore"
pubsub_name = "pubsub"

BATCH_SIZE = 8
MAX_SECTION_LENGTH = 1000
SENTENCE_SEARCH_LIMIT = 100
SECTION_OVERLAP = 100

def table_to_html(table):
    table_html = "<table>"
    rows = [sorted([cell for cell in table.cells if cell.row_index == i], key=lambda cell: cell.column_index) for i in range(table.row_count)]
    for row_cells in rows:
        table_html += "<tr>"
        for cell in row_cells:
            tag = "th" if (cell.kind == "columnHeader" or cell.kind == "rowHeader") else "td"
            cell_spans = ""
            if cell.column_span > 1: cell_spans += f" colSpan={cell.column_span}"
            if cell.row_span > 1: cell_spans += f" rowSpan={cell.row_span}"
            table_html += f"<{tag}{cell_spans}>{html.escape(cell.content)}</{tag}>"
        table_html +="</tr>"
    table_html += "</table>"
    return table_html

def process_with_form_recognizer(blob_content):
    offset = 0
    page_map = []

    try:
        # Get the Form Recognizer endpoint and key from Dapr secret store
        fr_endpoint = dapr_client.get_secret(store_name=secret_store, key="secretstore").secret["FORM_RECOGNIZER_ENDPOINT"]
        fr_key = dapr_client.get_secret(store_name=secret_store, key="secretstore").secret["FORM_RECOGNIZER_KEY"]

        # Send the stream to Azure Form Recognizer for analysis
        form_recognizer_client = DocumentAnalysisClient(
            endpoint=fr_endpoint,
            credential=AzureKeyCredential(fr_key),
            headers={"x-ms-useragent": "azure-ingestion-app/1.0.0"}
        )
        poller = form_recognizer_client.begin_analyze_document("prebuilt-layout", document=blob_content)
        form_recognizer_results = poller.result()

        for page_num, page in enumerate(form_recognizer_results.pages):
            tables_on_page = [table for table in form_recognizer_results.tables if table.bounding_regions[0].page_number == page_num + 1]

            # mark all positions of the table spans in the page
            page_offset = page.spans[0].offset
            page_length = page.spans[0].length
            table_chars = [-1]*page_length
            for table_id, table in enumerate(tables_on_page):
                for span in table.spans:
                    # replace all table spans with "table_id" in table_chars array
                    for i in range(span.length):
                        idx = span.offset - page_offset + i
                        if idx >=0 and idx < page_length:
                            table_chars[idx] = table_id

            # build page text by replacing characters in table spans with table html
            page_text = ""
            added_tables = set()
            for idx, table_id in enumerate(table_chars):
                if table_id == -1:
                    page_text += form_recognizer_results.content[page_offset + idx]
                elif table_id not in added_tables:
                    page_text += table_to_html(tables_on_page[table_id])
                    added_tables.add(table_id)

            page_text += " "
            page_map.append((page_num, offset, page_text))
            offset += len(page_text)

        return page_map

    except Exception as e:
        # Handle exceptions as needed
        print(f"An error occurred while processing with Form Recognizer: {e}", flush=True)
        return None

def split_text(page_map, filename):
    SENTENCE_ENDINGS = [".", "!", "?"]
    WORDS_BREAKS = [",", ";", ":", " ", "(", ")", "[", "]", "{", "}", "\t", "\n"]
    # print(f"Splitting '{filename}' into sections", flush=True)

    def find_page(offset):
        num_pages = len(page_map)
        for i in range(num_pages - 1):
            if offset >= page_map[i][1] and offset < page_map[i + 1][1]:
                return i
        return num_pages - 1

    all_text = "".join(p[2] for p in page_map)
    length = len(all_text)
    start = 0
    end = length
    while start + SECTION_OVERLAP < length:
        last_word = -1
        end = start + MAX_SECTION_LENGTH

        if end > length:
            end = length
        else:
            # Try to find the end of the sentence
            while end < length and (end - start - MAX_SECTION_LENGTH) < SENTENCE_SEARCH_LIMIT and all_text[end] not in SENTENCE_ENDINGS:
                if all_text[end] in WORDS_BREAKS:
                    last_word = end
                end += 1
            if end < length and all_text[end] not in SENTENCE_ENDINGS and last_word > 0:
                end = last_word # Fall back to at least keeping a whole word
        if end < length:
            end += 1

        # Try to find the start of the sentence or at least a whole word boundary
        last_word = -1
        while start > 0 and start > end - MAX_SECTION_LENGTH - 2 * SENTENCE_SEARCH_LIMIT and all_text[start] not in SENTENCE_ENDINGS:
            if all_text[start] in WORDS_BREAKS:
                last_word = start
            start -= 1
        if all_text[start] not in SENTENCE_ENDINGS and last_word > 0:
            start = last_word
        if start > 0:
            start += 1

        section_text = all_text[start:end]
        yield (section_text, find_page(start))

        last_table_start = section_text.rfind("<table")
        if (last_table_start > 2 * SENTENCE_SEARCH_LIMIT and last_table_start > section_text.rfind("</table")):
            # If the section ends with an unclosed table, we need to start the next section with the table.
            # If table starts inside SENTENCE_SEARCH_LIMIT, we ignore it, as that will cause an infinite loop for tables longer than MAX_SECTION_LENGTH
            # If last table starts inside SECTION_OVERLAP, keep overlapping
            start = min(end - SECTION_OVERLAP, start + last_table_start)
        else:
            start = end - SECTION_OVERLAP

    if start + SECTION_OVERLAP < end:
        yield (all_text[start:end], find_page(start))
        
def create_sections(filename, page_map, doc_id, ingestion_id):
    for i, (content, pagenum) in enumerate(split_text(page_map, filename)):
        section = {
            "id": f"{ingestion_id}-{doc_id}-section-{i}",
            "content": content,
            "category": "",
            "sourcepage": pagenum,
            "sourcefile": filename
        }
        yield section

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
    data = json.loads(event.data)

    ingestion_id = data["ingestion_id"]
    doc_id = data["doc_id"]
    blob_name = data["blob_name"]
    
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
        form_recognizer_result = process_with_form_recognizer(blob_content)

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
