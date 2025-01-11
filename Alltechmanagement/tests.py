from django.test import TestCase
from unittest.mock import patch
from meilisearch import Client

class MeilisearchUpdateTest(TestCase):
    def setUp(self):
        """
        Set up the Meilisearch client and mock data.
        """
        # Initialize the Meilisearch client with an API key
        self.client = Client(
            'https://meilisearch.gachara.store',  # Replace with your Meilisearch URL
            'LAgLVyTEqFhwWge2gQOl6mej8e9prqP1'  # Replace with your API key
        )
        self.index_name = 'Shop2Stock'

        # Create a new document
        self.document = {
            'id': 120000,
            'product_name': 'New Product',
            'price': 200,
            'quantity': 10
        }

        # Ensure the index exists and add the document
        self.client.index(self.index_name).add_documents([self.document])

    @patch('meilisearch.Client.index')
    def test_meilisearch_update_document(self, mock_index):
        """
        Test updating a document in Meilisearch.
        """
        # Mock the response for update_documents
        mock_response = {'updateId': 1, 'status': 'enqueued'}
        mock_index.return_value.update_documents.return_value = mock_response

        # Data to update
        update_data = {
            'id': 120000,
            'product_name': 'Updated Product Name',
            'price': 250,
            'quantity': 5
        }

        # Perform the update
        response = self.client.index(self.index_name).update_documents([update_data])

        # Assert that the mocked method was called with the correct data
        mock_index.return_value.update_documents.assert_called_once_with([update_data])

        # Assert that the response matches the mocked response
        self.assertEqual(response, mock_response)

    def test_real_meilisearch_update_document(self):
        """
        Test updating a document using the actual Meilisearch client.
        """
        # Data to update
        update_data = {
            'id': 120000,
            'product_name': 'Updated Product Name',
            'price': 250,
            'quantity': 5
        }

        # Perform the update
        response = self.client.index(self.index_name).update_documents([update_data])

        # Fetch the updated document
        updated_document = self.client.index(self.index_name).get_document(120000)

        # Assert that the document was updated correctly
        self.assertEqual(updated_document['product_name'], 'Updated Product Name')
        self.assertEqual(updated_document['price'], 250)
        self.assertEqual(updated_document['quantity'], 5)
