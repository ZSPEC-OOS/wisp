from packages.common.models import Passage
from packages.search.pipeline import rerank_passages


def test_rerank_prefers_query_terms():
    passages = [
        Passage(text="Cats sleep on mats", source_url="https://a.test"),
        Passage(text="BM25 ranking improves lexical matching", source_url="https://b.test"),
    ]
    out = rerank_passages("bm25 lexical ranking", passages)
    assert out[0].text.startswith("BM25")
