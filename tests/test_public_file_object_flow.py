import importlib
import secrets
from pathlib import Path
from fastapi.testclient import TestClient

from st_recruitment_svc.storage import build_relative_path, write_file
from st_recruitment_svc.models.base import User, Visibility, FilePurpose
from st_recruitment_svc.storage import create_file_object
from st_recruitment_svc import config


def _reload_config_and_app():
    import st_recruitment_svc.config as cfg
    importlib.reload(cfg)
    import st_recruitment_svc.app as app_mod
    importlib.reload(app_mod)
    return app_mod.app


def test_public_file_object_flow(client: TestClient, db_session, tmp_path, monkeypatch):
    # Prepare public dir and ensure app mounts it by reloading config/app
    public_dir = tmp_path / "public"
    public_dir.mkdir(parents=True)

    monkeypatch.setenv("PUBLIC_FILES_DIR", str(public_dir))

    app = _reload_config_and_app()

    # create a user to own the file
    user = User(id=secrets.token_hex(8), email="u@example.com", password_hash="x", role="company")
    db_session.add(user)
    db_session.commit()

    # Build storage path for a public company logo
    rel = build_relative_path(Visibility.public, FilePurpose.company_logo, user.id, "fileid123", original_filename="logo.txt")
    data = b"hello-public"

    # Persist bytes into the PUBLIC_FILES_DIR
    abs_path, size = write_file(str(public_dir), rel, data)

    # Create DB row for file object (commits)
    fo = create_file_object(
        db_session,
        owner_user_id=user.id,
        visibility=Visibility.public,
        purpose=FilePurpose.company_logo,
        content_type="image/png",
        size_bytes=size,
        storage_path=rel,
        original_filename="logo.txt",
    )

    assert fo is not None
    assert fo.storage_path == rel
    assert fo.visibility == Visibility.public.value

    public_url = f"/public/{rel}"

    # Ensure the mounted app serves the bytes at /public/{storage_path}
    with TestClient(app) as client:
        resp = client.get(public_url)
        assert resp.status_code == 200
        assert resp.content == data
