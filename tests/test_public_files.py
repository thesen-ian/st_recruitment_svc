import importlib
from pathlib import Path
from fastapi.testclient import TestClient

import pytest


def _reload_config_and_app():
    # reload config then app to pick up env changes
    import st_recruitment_svc.config as cfg
    importlib.reload(cfg)
    import st_recruitment_svc.app as app_mod
    importlib.reload(app_mod)
    return app_mod.app


def test_serves_public_file(tmp_path, monkeypatch):
    public_dir = tmp_path / "public"
    (public_dir / "company").mkdir(parents=True)
    logo = public_dir / "company" / "logo.txt"
    logo.write_text("hello")

    monkeypatch.setenv("PUBLIC_FILES_DIR", str(public_dir))

    app = _reload_config_and_app()

    with TestClient(app) as client:
        resp = client.get("/public/company/logo.txt")
        assert resp.status_code == 200
        assert resp.content == b"hello"


def test_autocreate_missing_directory(tmp_path, monkeypatch):
    missing_dir = tmp_path / "missing_public"
    assert not missing_dir.exists()

    monkeypatch.setenv("PUBLIC_FILES_DIR", str(missing_dir))

    app = _reload_config_and_app()

    # The directory should have been created during app import/startup
    assert missing_dir.exists() and missing_dir.is_dir()

    # Now create a file inside and ensure it's served
    (missing_dir / "company").mkdir(parents=True, exist_ok=True)
    f = missing_dir / "company" / "x.txt"
    f.write_text("data")

    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        resp = client.get("/public/company/x.txt")
        assert resp.status_code == 200
        assert resp.content == b"data"


def test_missing_config_fails_fast(monkeypatch):
    # Simulate explicit misconfiguration by setting empty value
    monkeypatch.setenv("PUBLIC_FILES_DIR", "")

    # Reloading is expected to raise RuntimeError
    import importlib
    import st_recruitment_svc.config as cfg
    importlib.reload(cfg)

    import st_recruitment_svc.app as app_mod
    with pytest.raises(RuntimeError) as exc:
        importlib.reload(app_mod)
    assert "PUBLIC_FILES_DIR" in str(exc.value)
