"""End-to-end extraction from cloud storage (S3, Azure, GCS).

Verifies the full pipeline: Cloud Storage → Reader → Encoder → (mock) LLM → Pydantic.
LLM is mocked to avoid API costs — the point is proving cloud I/O works through Extractor.

Run with:
    cd tests/integration && docker compose up -d
    uv run pytest tests/integration/test_extractor_cloud.py -v
"""

from unittest.mock import AsyncMock, MagicMock, patch

from pydantic import BaseModel

from xlstruct.extractor import Extractor

from .conftest import (
    AZURITE_ACCOUNT_KEY,
    AZURITE_ACCOUNT_NAME,
    FAKE_GCS_ENDPOINT,
    MINIO_ENDPOINT,
    MINIO_KEY,
    MINIO_SECRET,
)

# * Test schema

class Product(BaseModel):
    name: str
    price: float


EXPECTED = [
    Product(name="Apple", price=1.5),
    Product(name="Banana", price=0.75),
]


# * Helper

def _make_extractor() -> Extractor:
    """Create Extractor with mocked LLM client."""
    with patch(
        "xlstruct.extraction.engine.ExtractionEngine._build_client",
        return_value=MagicMock(),
    ):
        return Extractor()


# * S3 (MinIO)

class TestS3Extractor:
    async def test_extract_from_s3(self, s3_setup: str):
        """Full pipeline: MinIO → Reader → Encoder → mock LLM → Product list."""
        extractor = _make_extractor()
        with patch.object(extractor._engine, "extract", new_callable=AsyncMock) as mock_extract:
            mock_extract.return_value = EXPECTED
            results = await extractor.extract(
                s3_setup,
                Product,
                key=MINIO_KEY,
                secret=MINIO_SECRET,
                client_kwargs={"endpoint_url": MINIO_ENDPOINT},
            )

        assert len(results) == 2
        assert results[0].name == "Apple"
        assert results[1].price == 0.75

        # ^ Verify encoded text was passed to LLM engine
        encoded_text = mock_extract.call_args[0][0]
        assert "Products" in encoded_text

    async def test_load_workbook_from_s3(self, s3_setup: str):
        """Verify workbook metadata loaded from S3."""
        extractor = _make_extractor()
        workbook = await extractor._load_workbook(
            s3_setup,
            key=MINIO_KEY,
            secret=MINIO_SECRET,
            client_kwargs={"endpoint_url": MINIO_ENDPOINT},
        )
        assert workbook.file_name == "test.xlsx"
        assert len(workbook.sheets) == 1
        assert workbook.sheets[0].name == "Products"


# * Azure Blob (Azurite)

class TestAzureExtractor:
    async def test_extract_from_azure(self, azure_setup: str):
        """Full pipeline: Azurite → Reader → Encoder → mock LLM → Product list."""
        extractor = _make_extractor()
        with patch.object(extractor._engine, "extract", new_callable=AsyncMock) as mock_extract:
            mock_extract.return_value = EXPECTED
            results = await extractor.extract(
                azure_setup,
                Product,
                account_name=AZURITE_ACCOUNT_NAME,
                account_key=AZURITE_ACCOUNT_KEY,
                connection_string=(
                    "DefaultEndpointsProtocol=http;"
                    f"AccountName={AZURITE_ACCOUNT_NAME};"
                    f"AccountKey={AZURITE_ACCOUNT_KEY};"
                    "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
                ),
            )

        assert len(results) == 2
        assert results[0].name == "Apple"

        encoded_text = mock_extract.call_args[0][0]
        assert "Products" in encoded_text

    async def test_load_workbook_from_azure(self, azure_setup: str):
        """Verify workbook metadata loaded from Azurite."""
        extractor = _make_extractor()
        workbook = await extractor._load_workbook(
            azure_setup,
            account_name=AZURITE_ACCOUNT_NAME,
            account_key=AZURITE_ACCOUNT_KEY,
            connection_string=(
                "DefaultEndpointsProtocol=http;"
                f"AccountName={AZURITE_ACCOUNT_NAME};"
                f"AccountKey={AZURITE_ACCOUNT_KEY};"
                "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
            ),
        )
        assert workbook.file_name == "test.xlsx"
        assert len(workbook.sheets) == 1


# * GCS (fake-gcs-server)

class TestGCSExtractor:
    async def test_extract_from_gcs(self, gcs_setup: str):
        """Full pipeline: fake-gcs → Reader → Encoder → mock LLM → Product list."""
        extractor = _make_extractor()
        with patch.object(extractor._engine, "extract", new_callable=AsyncMock) as mock_extract:
            mock_extract.return_value = EXPECTED
            results = await extractor.extract(
                gcs_setup,
                Product,
                token="anon",
                endpoint_url=FAKE_GCS_ENDPOINT,
            )

        assert len(results) == 2
        assert results[1].name == "Banana"

        encoded_text = mock_extract.call_args[0][0]
        assert "Products" in encoded_text

    async def test_load_workbook_from_gcs(self, gcs_setup: str):
        """Verify workbook metadata loaded from fake-gcs."""
        extractor = _make_extractor()
        workbook = await extractor._load_workbook(
            gcs_setup,
            token="anon",
            endpoint_url=FAKE_GCS_ENDPOINT,
        )
        assert workbook.file_name == "test.xlsx"
        assert len(workbook.sheets) == 1
