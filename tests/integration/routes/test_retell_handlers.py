
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.app.retell.handlers import create_patient
from src.app.nexhealth.exceptions import NexHealthAPIError

@pytest.mark.asyncio
async def test_create_patient_success():
    """Test successful patient creation via Retell handler."""
    mock_args = {
        "first_name": "John",
        "last_name": "Doe",
        "email": "john.doe@example.com",
        "phone_number": "555-0123",
        "date_of_birth": "1990-01-01",
        "location_id": 123,
        "subdomain": "test-dental",
        "provider_id": 456
    }

    mock_response = {
        "code": True,
        "data": {
            "user": {
                "id": 789,
                "first_name": "John",
                "email": "john.doe@example.com"
            }
        }
    }

    with patch("src.app.retell.handlers._get_nexhealth_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = await create_patient(mock_args)

        assert result["success"] is True
        assert result["patient_id"] == 789
        assert "created successfully" in result["message"]

        # Verify client call structure
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "/patients"
        assert call_args[1]["params"] == {"subdomain": "test-dental", "location_id": 123}
        assert call_args[1]["json"]["patient"]["first_name"] == "John"
        assert call_args[1]["json"]["provider"]["provider_id"] == 456

@pytest.mark.asyncio
async def test_create_patient_missing_fields():
    """Test validation failure for missing fields."""
    mock_args = {
        "first_name": "John",
        # Missing other required fields
    }

    result = await create_patient(mock_args)

    assert "error" in result
    assert "is required" in result["error"]

@pytest.mark.asyncio
async def test_create_patient_api_failure():
    """Test handling of upstream API failure."""
    mock_args = {
        "first_name": "John",
        "last_name": "Doe",
        "email": "john.doe@example.com",
        "phone_number": "555-0123",
        "date_of_birth": "1990-01-01",
        "location_id": 123,
        "subdomain": "test-dental",
        "provider_id": 456
    }

    with patch("src.app.retell.handlers._get_nexhealth_client") as mock_get_client:
        mock_client = AsyncMock()
        mock_client.post.side_effect = Exception("API Error")
        mock_get_client.return_value = mock_client

        result = await create_patient(mock_args)

        assert result["success"] is False
        assert "500" in result["error"] or "API Error" in result["error"]
