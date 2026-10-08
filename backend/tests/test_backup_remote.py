"""The remote copy, against a small S3 stand-in on localhost."""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.services.backup import remote
from app.services.backup.remote import RemoteError
from app.services.backup.settings import RemoteConfig

NAME = "megooci-backup-20261008-030000-manual.mcbak"


class FakeS3(BaseHTTPRequestHandler):
    """Path-style S3: PUT, GET and DELETE of /bucket/key. Knows one bucket."""

    objects: dict[str, bytes] = {}
    requests: list[tuple[str, str]] = []

    def log_message(self, *args):
        pass

    def _answer(self, status, body=b""):
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        if body:
            self.send_header("Content-Type", "application/xml")
        self.end_headers()
        self.wfile.write(body)

    def _handle(self):
        type(self).requests.append((self.command, self.path))
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if "authorization" not in {key.lower() for key in self.headers}:
            return self._answer(403, b"<Error><Code>AccessDenied</Code><Message>unsigned</Message></Error>")
        bucket, _, key = self.path.lstrip("/").partition("/")
        key = key.split("?")[0]
        if bucket != "backups":
            return self._answer(404, (
                b"<Error><Code>NoSuchBucket</Code>"
                b"<Message>The specified bucket does not exist</Message></Error>"))
        if self.command == "PUT":
            type(self).objects[key] = body
            return self._answer(200)
        if self.command == "DELETE":
            type(self).objects.pop(key, None)
            return self._answer(204)
        self._answer(405)

    do_PUT = do_DELETE = do_GET = do_HEAD = _handle


@pytest.fixture
def s3():
    FakeS3.objects, FakeS3.requests = {}, []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeS3)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield FakeS3, f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def _config(endpoint, **overrides):
    fields = {"enabled": True, "endpoint_url": endpoint, "region": "eu-test", "bucket": "backups",
              "prefix": "ci", "access_key_id": "AKIATEST", "secret_access_key": "secret"}
    return RemoteConfig(**{**fields, **overrides})


async def test_upload_puts_the_file_under_the_prefix(s3):
    fake, endpoint = s3

    await remote.upload(_config(endpoint), NAME, b"backup bytes")

    assert fake.objects == {f"ci/{NAME}": b"backup bytes"}


async def test_upload_without_a_prefix(s3):
    fake, endpoint = s3
    await remote.upload(_config(endpoint, prefix=""), NAME, b"x")
    assert list(fake.objects) == [NAME]


async def test_delete_removes_the_object(s3):
    fake, endpoint = s3
    await remote.upload(_config(endpoint), NAME, b"x")

    await remote.delete(_config(endpoint), NAME)

    assert fake.objects == {}


async def test_check_connection_writes_and_removes_a_small_object(s3):
    fake, endpoint = s3

    await remote.check_connection(_config(endpoint))

    assert fake.objects == {}
    assert [method for method, _ in fake.requests] == ["PUT", "DELETE"]
    assert all(path.startswith("/backups/ci/.megooci-connection-test") for _, path in fake.requests)


async def test_a_missing_bucket_is_reported_with_the_storages_reason(s3):
    _, endpoint = s3

    with pytest.raises(RemoteError) as exc:
        await remote.upload(_config(endpoint, bucket="nope"), NAME, b"x")

    assert str(exc.value) == "NoSuchBucket: The specified bucket does not exist"


async def test_check_connection_fails_the_same_way(s3):
    _, endpoint = s3
    with pytest.raises(RemoteError) as exc:
        await remote.check_connection(_config(endpoint, bucket="nope"))
    assert "NoSuchBucket" in str(exc.value)


async def test_an_unreachable_endpoint_is_a_remote_error_not_a_crash(monkeypatch):
    monkeypatch.setattr(remote, "MAX_ATTEMPTS", 1)
    monkeypatch.setattr(remote, "CONNECT_TIMEOUT_SECONDS", 1)
    config = _config("http://127.0.0.1:1")

    with pytest.raises(RemoteError) as exc:
        await remote.upload(config, NAME, b"x")

    assert str(exc.value) and len(str(exc.value)) <= 300
    assert "secret" not in str(exc.value)
