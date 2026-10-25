from app.config import Settings


def test_cors_origins_parse_from_comma_separated_string() -> None:
    settings = Settings(cors_allow_origins="http://localhost:5173, https://example.org")

    assert settings.cors_allow_origins == ["http://localhost:5173", "https://example.org"]


def test_secrets_default_to_empty_so_the_app_starts_without_them() -> None:
    settings = Settings(_env_file=None)

    assert settings.anthropic_api_key == ""
    assert settings.hf_api_token == ""
    assert settings.app_env == "dev"


def test_settings_load_from_a_real_dotenv_file(tmp_path) -> None:  # noqa: ANN001
    """Regression: the documented .env format must actually boot the app.

    pydantic-settings JSON-decodes list fields from dotenv sources before
    validators run; a plain comma string crashed startup the first time a
    real .env existed (2026-10-23).
    """
    env_file = tmp_path / ".env"
    env_file.write_text(
        "APP_ENV=dev\n"
        "CORS_ALLOW_ORIGINS=http://localhost:5173, https://example.org\n"
        "ANTHROPIC_API_KEY=test-value\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.cors_allow_origins == ["http://localhost:5173", "https://example.org"]
    assert settings.anthropic_api_key == "test-value"
