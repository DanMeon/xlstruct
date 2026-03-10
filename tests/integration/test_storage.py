"""Integration tests for cloud storage backends.

Run with:
    cd tests/integration && docker compose up -d
    uv run pytest tests/integration/ -v
"""

import pytest

from xlstruct.storage import read_file

from .conftest import (
    AZURITE_ACCOUNT_KEY,
    AZURITE_ACCOUNT_NAME,
    FAKE_GCS_ENDPOINT,
    MINIO_ENDPOINT,
    MINIO_KEY,
    MINIO_SECRET,
)

# * S3 (MinIO)


class TestS3Storage:
    async def test_read_from_s3(self, s3_setup: str, sample_xlsx_bytes: bytes):
        """Read xlsx from MinIO via s3:// protocol."""
        data = await read_file(
            s3_setup,
            key=MINIO_KEY,
            secret=MINIO_SECRET,
            client_kwargs={"endpoint_url": MINIO_ENDPOINT},
        )
        assert data == sample_xlsx_bytes

    async def test_s3_file_not_found(self):
        """Non-existent S3 key raises StorageError."""
        from xlstruct.exceptions import StorageError

        with pytest.raises(StorageError, match="not found|Failed"):
            await read_file(
                "s3://test-bucket/does-not-exist.xlsx",
                key=MINIO_KEY,
                secret=MINIO_SECRET,
                client_kwargs={"endpoint_url": MINIO_ENDPOINT},
            )


# * Azure Blob (Azurite)


class TestAzureStorage:
    async def test_read_from_azure(self, azure_setup: str, sample_xlsx_bytes: bytes):
        """Read xlsx from Azurite via az:// protocol."""
        data = await read_file(
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
        assert data == sample_xlsx_bytes

    async def test_azure_file_not_found(self):
        """Non-existent blob raises StorageError."""
        from xlstruct.exceptions import StorageError

        with pytest.raises(StorageError, match="not found|Failed"):
            await read_file(
                "az://test-container/does-not-exist.xlsx",
                account_name=AZURITE_ACCOUNT_NAME,
                account_key=AZURITE_ACCOUNT_KEY,
                connection_string=(
                    "DefaultEndpointsProtocol=http;"
                    f"AccountName={AZURITE_ACCOUNT_NAME};"
                    f"AccountKey={AZURITE_ACCOUNT_KEY};"
                    "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
                ),
            )


# * GCS (fake-gcs-server)


class TestGCSStorage:
    async def test_read_from_gcs(self, gcs_setup: str, sample_xlsx_bytes: bytes):
        """Read xlsx from fake-gcs-server via gs:// protocol."""
        data = await read_file(
            gcs_setup,
            token="anon",
            endpoint_url=FAKE_GCS_ENDPOINT,
        )
        assert data == sample_xlsx_bytes

    async def test_gcs_file_not_found(self):
        """Non-existent GCS object raises StorageError."""
        from xlstruct.exceptions import StorageError

        with pytest.raises(StorageError, match="not found|Failed"):
            await read_file(
                "gs://test-bucket/does-not-exist.xlsx",
                token="anon",
                endpoint_url=FAKE_GCS_ENDPOINT,
            )
