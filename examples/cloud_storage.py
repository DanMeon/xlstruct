"""Cloud storage example: Read Excel from S3/Azure/GCS.

Usage:
    uv run python examples/cloud_storage.py

Requires:
    - Provider-specific credentials configured
    - Install extras: uv add xlstruct[s3] / xlstruct[azure] / xlstruct[gcs]
"""

import asyncio

from pydantic import BaseModel

from xlstruct.extractor import Extractor


class SalesRecord(BaseModel):
    region: str
    product: str
    quantity: int
    revenue: float


async def example_s3():
    """Extract from S3."""
    extractor = Extractor(provider="openai/gpt-4o")
    results = await extractor.extract(
        "s3://my-bucket/reports/sales_q4.xlsx",
        SalesRecord,
        # ^ Pass S3-specific options
        anon=False,
    )
    for r in results:
        print(f"  {r.region}: {r.product} — {r.quantity} units, ${r.revenue:.2f}")


async def example_azure():
    """Extract from Azure Blob Storage."""
    extractor = Extractor(
        provider="openai/gpt-4o",
        storage_options={"account_name": "myaccount"},
    )
    results = await extractor.extract(
        "az://my-container/reports/sales.xlsx",
        SalesRecord,
    )
    for r in results:
        print(f"  {r.region}: {r.product}")


async def example_gcs():
    """Extract from Google Cloud Storage."""
    extractor = Extractor(provider="gemini/gemini-2.0-flash")
    results = await extractor.extract(
        "gs://my-bucket/data/report.xlsx",
        SalesRecord,
        instructions="Extract sales records. Revenue should be in USD.",
    )
    for r in results:
        print(f"  {r.region}: {r.product}")


async def main():
    print("=== S3 Example ===")
    # await example_s3()
    print("  (Skipped — configure AWS credentials first)")

    print("\n=== Azure Example ===")
    # await example_azure()
    print("  (Skipped — configure Azure credentials first)")

    print("\n=== GCS Example ===")
    # await example_gcs()
    print("  (Skipped — configure GCP credentials first)")

    print("\nUncomment the desired function call after configuring credentials.")


if __name__ == "__main__":
    asyncio.run(main())
