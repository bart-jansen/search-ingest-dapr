import time
from azure.core.credentials import AzureKeyCredential
from azure.search.documents.indexes import SearchIndexClient, SearchIndexerClient
from azure.core.exceptions import ResourceNotFoundError
from azure.search.documents.indexes.models import (
    SearchIndex, SearchIndexer, HnswParameters, SearchField, SearchFieldDataType,
    SearchIndexerDataSourceConnection, SearchIndexerDataContainer, VectorSearch,
    VectorSearchAlgorithmConfiguration
)

class AzureSearchIndex:
    def __init__(self, service_name, search_key, blob_connection_string, blob_container, blob_items_folder):
        self.service_name = service_name
        self.search_key = search_key
        self.blob_connection_string = blob_connection_string
        self.blob_container = blob_container
        self.blob_items_folder = blob_items_folder
        self.creds = AzureKeyCredential(self.search_key)
        self.search_index_client = SearchIndexClient(
            endpoint=f"https://{self.service_name}.search.windows.net/",
            credential=self.creds
        )
        self.search_indexer_client = SearchIndexerClient(
            endpoint=f"https://{self.service_name}.search.windows.net/",
            credential=self.creds
        )

    def create_datasource(self, data_source_name):
        try:
            # Try to get the datasource to check if it already exists
            self.search_indexer_client.get_data_source_connection(data_source_name)
            print(f"Datasource '{data_source_name}' already exists.", flush=True)
        except ResourceNotFoundError:
            # If not found, create the datasource
            data_source = SearchIndexerDataSourceConnection(
                name=data_source_name,
                type="azureblob",
                connection_string=self.blob_connection_string,
                container=SearchIndexerDataContainer(name=self.blob_container, query=self.blob_items_folder)
            )
            self.search_indexer_client.create_or_update_data_source_connection(data_source_connection=data_source)
            print(f"Datasource '{data_source_name}' created.", flush=True)

    def create_index(self, index_name):
        try:
            # Try to get the index to check if it already exists
            self.search_index_client.get_index(index_name)
            print(f"Index '{index_name}' already exists.", flush=True)
        except ResourceNotFoundError:
            # If not found, define and create the index
            index_definition = SearchIndex(
                name=index_name,
                fields=[
                    SearchField(name="id", type=SearchFieldDataType.String, key=True),
                    SearchField(name="content", type=SearchFieldDataType.String, filterable=True, sortable=True),
                    SearchField(name="category", type=SearchFieldDataType.String, filterable=True, sortable=True, facetable=True),
                    SearchField(name="sourcepage", type=SearchFieldDataType.String, filterable=True, facetable=True),
                    SearchField(name="sourcefile", type=SearchFieldDataType.String, filterable=True, facetable=True),
                    SearchField(name="summaries", type=SearchFieldDataType.String, filterable=True),
                    SearchField(name="keyphrases", type=SearchFieldDataType.Collection(SearchFieldDataType.String)),
                    SearchField(
                        name="embeddings",
                        type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                        hidden=False,
                        searchable=True,
                        filterable=False,
                        sortable=False,
                        facetable=False,
                        vector_search_dimensions=1536,
                        vector_search_configuration="default",
                    )
                ],
                vector_search=VectorSearch(
                    algorithm_configurations=[
                        VectorSearchAlgorithmConfiguration(
                            name="default",
                            kind="hnsw",
                            hnsw_parameters=HnswParameters(metric="cosine")
                        )
                    ]
                )
            )

            # Create the index
            self.search_index_client.create_index(index=index_definition)
            print(f"Index '{index_name}' created.", flush=True)

    def create_indexer(self, indexer_name, data_source_name, index_name):
        try:
            # Try to get the indexer to check if it already exists
            self.search_indexer_client.get_indexer(indexer_name)
            print(f"Indexer '{indexer_name}' already exists.", flush=True)
        except ResourceNotFoundError:
            # If not found, define and create the indexer
            indexer = SearchIndexer(
                name=indexer_name,
                data_source_name=data_source_name,
                target_index_name=index_name,
                field_mappings=[],
                output_field_mappings=[],
                parameters={"configuration": {"parsingMode": "jsonArray"}}
            )
            # Create or update the indexer
            self.search_indexer_client.create_or_update_indexer(indexer=indexer)
            print(f"Indexer '{indexer_name}' created.", flush=True)

    def run_indexer(self, indexer_name, callback=None):
        self.search_indexer_client.run_indexer(indexer_name)
        print(f"Indexer '{indexer_name}' ran.", flush=True)

        # Wait some time for indexer to start, todo: cleaner method
        time.sleep(5)

        # Monitor indexer status, with interval of 5 seconds
        # todo: add timeout
        while True:
            indexer_status = self.search_indexer_client.get_indexer_status(indexer_name)
            if indexer_status.last_result.status != 'success':
                print(f"⌛️ Indexer status: {indexer_status.status} with Item Count: {indexer_status.last_result.item_count}", flush=True)
                time.sleep(5)
            else:
                print(f"🏁 Indexer status: {indexer_status.last_result.status}. Total item count: {indexer_status.last_result.item_count}", flush=True)
                if callback:
                    callback(indexer_status)
                break