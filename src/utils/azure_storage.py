import base64
import hashlib
import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContainerClient, ContentSettings

from src.utils.helpers import PROJECT_ROOT

load_dotenv(PROJECT_ROOT / ".env")


@lru_cache(maxsize=1)
def get_service_client() -> BlobServiceClient:
    """Connect to the Storage Account with the current Azure login (az login)."""
    account_name = os.getenv("AZURE_STORAGE_ACCOUNT_NAME")
    if not account_name:
        raise ValueError("AZURE_STORAGE_ACCOUNT_NAME not found in .env")

    return BlobServiceClient(
        account_url=f"https://{account_name}.blob.core.windows.net",
        credential=DefaultAzureCredential(),
    )


def check_access() -> str | None:
    """Try one small call. Returns None if Azure works, otherwise the error message."""
    try:
        # Listing containers only needs the "Storage Blob Data" role (account info needs more)
        next(iter(get_service_client().list_containers(results_per_page=1)), None)
        return None
    except Exception as error:  # noqa: BLE001 — any login/permission/network problem
        return str(error).splitlines()[0]


def get_container(name: str) -> ContainerClient:
    """Return the container, creating it the first time."""
    container = get_service_client().get_container_client(name)
    try:
        container.create_container()
    except ResourceExistsError:
        pass
    return container


def file_md5(path: Path) -> bytes:
    digest = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.digest()


def upload_file(local_file: Path, container: ContainerClient, blob_name: str) -> str:
    """Upload one file without creating duplicates.

    The blob name is fixed (same local file -> same blob), so a file is never stored twice.
    - blob missing          -> "uploaded"
    - same content (MD5)    -> "skipped"  (nothing sent)
    - content changed       -> "updated"  (blob overwritten)
    """
    local_file = Path(local_file)
    md5 = file_md5(local_file)
    blob = container.get_blob_client(blob_name)

    try:
        remote_md5 = blob.get_blob_properties().content_settings.content_md5
    except ResourceNotFoundError:
        remote_md5 = None

    if remote_md5 is not None and bytes(remote_md5) == md5:
        return "skipped"

    with open(local_file, "rb") as data:
        blob.upload_blob(data, overwrite=True, content_settings=ContentSettings(content_md5=md5))
    return "uploaded" if remote_md5 is None else "updated"


def download_file(container_name: str, blob_name: str, local_file: Path) -> bool:
    """Download one blob to local_file. Returns False if the blob doesn't exist."""
    blob = get_service_client().get_container_client(container_name).get_blob_client(blob_name)
    try:
        data = blob.download_blob().readall()
    except ResourceNotFoundError:
        return False
    local_file = Path(local_file)
    local_file.parent.mkdir(parents=True, exist_ok=True)
    local_file.write_bytes(data)
    return True


def upload_folder(local_dir: Path, container_name: str, prefix: str = "", pattern: str = "*") -> dict[str, str]:
    """Upload every file in local_dir (recursively) to <container>/<prefix>/<relative path>."""
    local_dir = Path(local_dir)
    if not local_dir.exists():
        return {}

    container = get_container(container_name)
    results = {}
    for path in sorted(p for p in local_dir.rglob(pattern) if p.is_file()):
        relative = path.relative_to(local_dir).as_posix()
        blob_name = f"{prefix}/{relative}" if prefix else relative
        results[blob_name] = upload_file(path, container, blob_name)
    return results


def upload_to_bronze(local_file, source_name) -> str:
    """Upload one bronze file to bronze/<source>/<file name> (kept for existing callers)."""
    local_file = Path(local_file)
    return upload_file(local_file, get_container("bronze"), f"{source_name}/{local_file.name}")
