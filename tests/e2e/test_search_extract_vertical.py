import pytest

from apps.api.dependencies.services import extract_service, search_service
from packages.search.pipeline import rerank_passages


@pytest.mark.asyncio
async def test_vertical_slice_runs():
    results = await search_service.search("Python programming", max_results=3)
    if not results:
        pytest.skip("No network search results")
    docs = await extract_service.extract_many([str(results[0].url)], format="text")
    passages = docs[0].passages if docs else []
    _ = rerank_passages("python", passages)
    assert docs is not None
