"""Fuzzy product search.

Postgres trigram matching rather than a separate search service. The
Meilisearch index this replaces had become write-only -- every mutation updated
it and nothing queried it -- so it cost a container and a sync path while
answering nothing.

What trigram buys over a plain ILIKE is tolerance for how people actually type
at a counter: "ifone", "samsng", "reddmi" all still find the right item, and
ILIKE finds none of them.
"""
from django.conf import settings
from django.db.models import F, FloatField, Func, Q, Value

# Below this, matches are noise: at 0.2 a three-letter query starts matching
# most of the catalogue. Tuned against real product names, which are short and
# share a lot of trigrams ("Screen" appears in every one of them).
SIMILARITY_THRESHOLD = 0.3


def _qualified(name):
    """Schema-qualify a pg_trgm function.

    Supabase installs extensions into `public` while this project's tables live
    in their own schema. The name is qualified rather than relying on the
    search path alone so the query is correct even if the path changes.
    """
    schema = getattr(settings, 'DB_EXTENSIONS_SCHEMA', None)
    return f'{schema}.{name}' if schema else name


class WordSimilarity(Func):
    """word_similarity(query, field).

    Word similarity, not plain similarity: it scores the query against the
    best-matching *word* in the field, so "ifone" matches "iPhone 12 Screen"
    strongly. Plain similarity compares whole strings and scores that pair low,
    because most of the field is words the query never mentions.
    """

    output_field = FloatField()

    def __init__(self, query, field):
        super().__init__(Value(query), F(field), function=_qualified('word_similarity'))


def search_products(queryset, query, field='product_name'):
    """Filter and rank a product queryset by a search term.

    Substring matches are kept unconditionally and ranked above fuzzy ones: if
    someone types an exact product name, that item must be first, and a
    similarity score alone does not guarantee it.
    """
    query = (query or '').strip()
    if not query:
        return queryset

    return (
        queryset
        .annotate(similarity=WordSimilarity(query, field))
        .filter(
            Q(**{f'{field}__icontains': query})
            | Q(similarity__gte=SIMILARITY_THRESHOLD)
        )
        .order_by('-similarity', field)
    )
