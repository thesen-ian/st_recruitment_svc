import importlib
import io
import secrets
from pathlib import Path
from fastapi.testclient import TestClient

from st_recruitment_svc.storage import persist_uploadfile_as_public
from st_recruitment_svc.models.base import User, Visibility, FilePurpose


def _reload_config_and_app():
    import st_recruitment_svc.config as cfg
    importlib.reload(cfg)
    import st_recruitment_svc.app as app_mod
    importlib.reload(app_mod)
    return app_mod.app


class SimpleUploadFile:
    """Minimal duck-typed UploadFile for synchronous tests."""
    def __init__(self, filename: str, data: bytes, content_type: str):
        self.filename = filename
        self.file = io.BytesIO(data)
        self.content_type = content_type


def test_persist_uploadfile_as_public(client: TestClient, db_session, tmp_path, monkeypatch):
    # Prepare public dir and ensure app mounts it by reloading config/app
    public_dir = tmp_path / "public"
    public_dir.mkdir(parents=True)

    monkeypatch.setenv("PUBLIC_FILES_DIR", str(public_dir))

    app = _reload_config_and_app()

    # create a user to own the file
    user = User(id=secrets.token_hex(8), email="u2@example.com", password_hash="x", role="company")
    db_session.add(user)
    db_session.commit()

    data = b"hello-from-upload"
    # construct a minimal UploadFile-like object with a BytesIO file-like
    upload_file = SimpleUploadFile(filename="logo.png", data=data, content_type="image/png")

    fo, public_url = persist_uploadfile_as_public(db_session, user.id, upload_file, FilePurpose.company_logo)

    assert fo is not None
    assert fo.storage_path is not None
    assert fo.visibility == Visibility.public.value

    # ensure file exists on disk
    abs_path = public_dir / Path(fo.storage_path)
    assert abs_path.exists()
    with open(abs_path, "rb") as f:
        assert f.read() == data

    # Ensure the mounted app serves the bytes at /public/{storage_path}
    with TestClient(app) as test_client:
        resp = test_client.get(public_url)
        assert resp.status_code == 200
        assert resp.content == data
