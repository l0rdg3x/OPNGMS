from app.models import AppSetting, Session, UserMfa, UserRecoveryCode


def test_models_are_registered_on_metadata():
    from app.models import Base
    tables = set(Base.metadata.tables)
    assert {"user_mfa", "user_recovery_code", "app_settings"} <= tables


def test_session_has_kind_column():
    assert "kind" in Session.__table__.columns
    assert Session.__table__.columns["kind"].default.arg == "full"


def test_mfa_model_columns():
    cols = set(UserMfa.__table__.columns.keys())
    assert {"user_id", "enabled", "totp_secret_enc", "confirmed_at", "last_used_step"} <= cols
    assert "code_hash" in UserRecoveryCode.__table__.columns
    assert {"key", "value"} <= set(AppSetting.__table__.columns.keys())
