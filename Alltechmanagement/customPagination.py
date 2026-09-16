from rest_framework.pagination import PageNumberPagination


class CustomPagination(PageNumberPagination):
    page_size = 12  # Number of items per page
    # Lets a caller ask for more than one page at a time (e.g. a "view all
    # low stock" screen) instead of having to walk `next` repeatedly.
    page_size_query_param = 'page_size'
    max_page_size = 200


class StandardResultsSetPagination(PageNumberPagination):
    page_size = 100
    page_size_query_param = 'page_size'
    max_page_size = 1000
