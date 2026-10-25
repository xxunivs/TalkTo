"""녹음·음성 파일 저장소.

로컬은 디스크에 두고, 운영은 S3 에 둔다. 바깥(브라우저·AI 서버)에는 파일을 그대로
열지 않고 만료되는 서명 주소만 준다. S3 의 presigned URL 과 같은 방식이라, 저장소를
바꿔도 부르는 쪽 코드는 그대로다.

AI 서버는 녹음을 이 서명 주소로 내려받는다(TranscriptionRequest.audio_url).

큰 파일(녹음 원본·영상)은 바이트로 메모리에 올리지 않고 파일로 주고받는다(put_file·download·
upload). 모두 막는(blocking) 호출이라 비동기 코드에서는 asyncio.to_thread 로 부른다.
"""

import hashlib
import hmac
import shutil
import time
from pathlib import Path
from typing import BinaryIO, Protocol
from urllib.parse import quote, urlencode

from app.core.config import Settings, get_settings

# 지울 수 있는 곳. 빈 값·'/' 처럼 버킷 전체를 가리키는 접두어로 모든 가족의 파일을 지우지 않게,
# 이 아래의 한 대화 상대·한 대화방(id 까지 있는 것)만 지운다.
DELETABLE_ROOTS = ("personas", "conversations")
COPY_CHUNK = 1024 * 1024


def check_prefix(prefix: str) -> str:
    """지워도 되는 접두어인지 본다(personas/<id>…·conversations/<id>…). 아니면 ValueError."""
    parts = (prefix or "").lstrip("/").split("/")
    if len(parts) < 2 or parts[0] not in DELETABLE_ROOTS or not parts[1].strip():
        raise ValueError("지울 수 없는 저장소 경로")
    return prefix


class Storage(Protocol):
    def put(self, key: str, content: bytes, content_type: str | None = None) -> None: ...

    def put_file(self, key: str, fileobj: BinaryIO, content_type: str | None = None) -> None: ...

    def get(self, key: str) -> bytes: ...

    def download(self, key: str, path: Path) -> None: ...

    def upload(self, key: str, path: Path, content_type: str | None = None) -> None: ...

    def exists(self, key: str) -> bool: ...

    def delete_prefix(self, prefix: str) -> None: ...

    def signed_url(self, key: str, ttl_seconds: int | None = None) -> str: ...


def _signature(key: str, expires: int, secret: str) -> str:
    message = f"{key}:{expires}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_signature(key: str, expires: int, signature: str) -> bool:
    if expires < int(time.time()):
        return False
    expected = _signature(key, expires, get_settings().jwt_secret)
    return hmac.compare_digest(expected, signature)


class LocalStorage:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.root = settings.local_media_dir.resolve()

    def path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("잘못된 파일 경로")
        return path

    def put(self, key: str, content: bytes, content_type: str | None = None) -> None:
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def put_file(self, key: str, fileobj: BinaryIO, content_type: str | None = None) -> None:
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fileobj.seek(0)
        with path.open("wb") as out:
            shutil.copyfileobj(fileobj, out, COPY_CHUNK)

    def get(self, key: str) -> bytes:
        return self.path(key).read_bytes()

    def download(self, key: str, path: Path) -> None:
        shutil.copyfile(self.path(key), path)

    def upload(self, key: str, path: Path, content_type: str | None = None) -> None:
        target = self.path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)

    def exists(self, key: str) -> bool:
        return self.path(key).is_file()

    def delete_prefix(self, prefix: str) -> None:
        target = self.path(check_prefix(prefix))
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.is_file():
            target.unlink(missing_ok=True)

    def signed_url(self, key: str, ttl_seconds: int | None = None) -> str:
        expires = int(time.time()) + (ttl_seconds or self.settings.signed_url_ttl_seconds)
        query = urlencode(
            {"exp": expires, "sig": _signature(key, expires, self.settings.jwt_secret)}
        )
        base = self.settings.public_base_url.rstrip("/")
        path = self.settings.local_media_public_path.rstrip("/")
        return f"{base}{path}/{quote(key)}?{query}"


class S3Storage:
    """운영용. boto3 는 EC2 의 IAM 역할로 인증한다(키를 코드·설정에 두지 않는다)."""

    def __init__(self, settings: Settings) -> None:
        import boto3
        from boto3.s3.transfer import TransferConfig

        if not settings.s3_bucket:
            raise RuntimeError("STORAGE_BACKEND=s3 이면 S3_BUCKET 이 필요합니다.")
        self.settings = settings
        self.bucket = settings.s3_bucket
        self.client = boto3.client("s3", region_name=settings.s3_region)
        # 큰 파일은 8MB 조각으로 나눠 보낸다. 한 번에 4조각까지(메모리 32MB 쯤)만 들고 있는다.
        self.transfer = TransferConfig(max_concurrency=4)

    @staticmethod
    def _extra(content_type: str | None) -> dict:
        return {"ContentType": content_type} if content_type else {}

    def put(self, key: str, content: bytes, content_type: str | None = None) -> None:
        extra = self._extra(content_type)
        self.client.put_object(Bucket=self.bucket, Key=key, Body=content, **extra)

    def put_file(self, key: str, fileobj: BinaryIO, content_type: str | None = None) -> None:
        fileobj.seek(0)
        self.client.upload_fileobj(
            fileobj, self.bucket, key, ExtraArgs=self._extra(content_type), Config=self.transfer
        )

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def download(self, key: str, path: Path) -> None:
        self.client.download_file(self.bucket, key, str(path), Config=self.transfer)

    def upload(self, key: str, path: Path, content_type: str | None = None) -> None:
        self.client.upload_file(
            str(path), self.bucket, key, ExtraArgs=self._extra(content_type), Config=self.transfer
        )

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
        except self.client.exceptions.ClientError:
            return False
        return True

    def delete_prefix(self, prefix: str) -> None:
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=check_prefix(prefix)):
            keys = [{"Key": item["Key"]} for item in page.get("Contents", [])]
            if keys:
                self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": keys})

    def signed_url(self, key: str, ttl_seconds: int | None = None) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=ttl_seconds or self.settings.signed_url_ttl_seconds,
        )


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        settings = get_settings()
        _storage = (
            S3Storage(settings) if settings.storage_backend == "s3" else LocalStorage(settings)
        )
    return _storage


def resolve_audio_url(value: str | None) -> str | None:
    """메시지에 저장한 저장소 키를 들을 수 있는 서명 주소로 바꾼다. 예전 주소는 그대로."""
    if not value or value.startswith(("http://", "https://", "/")):
        return value
    return get_storage().signed_url(value)
