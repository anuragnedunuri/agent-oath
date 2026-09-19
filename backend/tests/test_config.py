from app.config import Settings


def test_cors_origins_parse_from_comma_separated_string() -> None:
    settings = Settings(cors_allow_origins="http://localhost:5173, https://example.org")

    assert settings.cors_allow_origins == ["http://localhost:5173", "https://example.org"]


def test_secrets_default_to_empty_so_the_app_starts_without_them() -> None:
    settings = Settings(_env_file=None)

    assert settings.anthropic_api_key == ""
    assert settings.hf_api_token == ""
    assert settings.app_env == "dev"
