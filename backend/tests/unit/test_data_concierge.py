import pytest
import asyncio
from unittest.mock import AsyncMock, patch

from src.services.tools.registry import load_all_tools, get_tool
from src.services.specialists.registry import load_all_specialists, SPECIALIST_REGISTRY

def test_data_concierge_tool_registration():
    load_all_tools()
    tool = get_tool("data_concierge")
    assert tool is not None
    assert tool.name == "data_concierge"
    assert tool.input_schema["type"] == "object"
    assert "query" in tool.input_schema["properties"]
    assert "query" in tool.input_schema["required"]

def test_specialists_have_data_concierge():
    load_all_specialists()
    for name in ["overlay_artist", "illustrator", "copy_claims_checker", "script_writer"]:
        spec = SPECIALIST_REGISTRY[name]
        assert "data_concierge" in spec.allowed_tools

@pytest.mark.asyncio
async def test_data_concierge_execution():
    load_all_tools()
    tool = get_tool("data_concierge")
    
    with patch("src.services.knowledge.data_concierge_service.DataConciergeService.answer_query", new_callable=AsyncMock) as mock_answer:
        mock_answer.return_value = {
            "answer": "Samsung S26 Ultra price is ₹1,39,999.",
            "sources": ["product:Samsung S26 Ultra"],
            "has_data": True
        }
        res = await tool.run({"query": "What is the price of Samsung S26 Ultra?"}, context={"user_id": "test_user"})
        assert res.ok is True
        assert res.data["answer"] == "Samsung S26 Ultra price is ₹1,39,999."
        assert res.data["has_data"] is True
