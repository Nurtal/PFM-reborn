import stat

from config.bootstrap import load_or_create_secret_key


def test_secret_key_is_generated_once_with_private_permissions(tmp_path, monkeypatch):
    monkeypatch.delenv("PFM_SECRET_KEY", raising=False)
    first = load_or_create_secret_key(tmp_path)
    second = load_or_create_secret_key(tmp_path)
    assert first == second
    assert len(first) >= 50
    mode = stat.S_IMODE((tmp_path / "secret_key").stat().st_mode)
    assert mode == 0o600
    assert list(tmp_path.iterdir()) == [tmp_path / "secret_key"]


def test_env_secret_key_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("PFM_SECRET_KEY", "from-env")
    assert load_or_create_secret_key(tmp_path) == "from-env"
    assert not (tmp_path / "secret_key").exists()
