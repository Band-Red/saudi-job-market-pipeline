import os
from datetime import datetime

from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient


load_dotenv()


def upload_to_bronze(local_file, source_name):

    # نجيب اسم Storage Account من .env
    account_name = os.getenv("AZURE_STORAGE_ACCOUNT_NAME")

    if not account_name:
        raise ValueError(
            "AZURE_STORAGE_ACCOUNT_NAME not found in .env"
        )

    # رابط Azure Storage
    account_url = (
        f"https://{account_name}.blob.core.windows.net"
    )

    # يستخدم تسجيل دخول Azure الحالي
    credential = DefaultAzureCredential()

    # الاتصال بالـ Storage Account
    blob_service_client = BlobServiceClient(
        account_url=account_url,
        credential=credential
    )

    # نحدد Container اسمه bronze
    container_client = (
        blob_service_client
        .get_container_client("bronze")
    )

    # ناخذ الوقت الحالي
    now = datetime.now()

    # مثال: 2026-09-16
    date_folder = now.strftime("%Y-%m-%d")

    # مثال: 20260916_084530
    timestamp = now.strftime("%Y%m%d_%H%M%S")

    # نحدد مكان الملف داخل Azure
    blob_name = (
        f"{source_name}/"
        f"{date_folder}/"
        f"jobs_{timestamp}.csv"
    )

    # نفتح الملف الموجود على جهازنا
    with open(local_file, "rb") as data:

        # نرفعه إلى Azure
        container_client.upload_blob(
            name=blob_name,
            data=data,
            overwrite=False
        )

    print("Uploaded to Azure ✅")
    print("Blob:", blob_name)