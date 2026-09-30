"""Construct every top-level object.

Guards against the "attribute used before it was assigned in __init__" class of
start-up crash: if constructing something fails, this test fails before a demo does.
New top-level objects get added here as each phase lands.
"""

import importlib
import pkgutil

import trustagent
from trustagent.config import Settings, get_settings
from trustagent.db.session import get_engine, get_sessionmaker


def test_every_module_imports():
    for module in pkgutil.walk_packages(trustagent.__path__, prefix="trustagent."):
        if ".migrations" in module.name:
            continue  # Alembic scripts only run inside an Alembic context
        if module.name == "trustagent.ui.app":
            continue  # a Streamlit script, run by `streamlit run`; covered by the AppTest tests
        importlib.import_module(module.name)


def test_settings_construct_with_defaults():
    settings = Settings(_env_file=None)
    assert settings.embedding_dim > 0
    assert settings.psycopg_conninfo.startswith("postgresql://")
    assert settings.contracts_dir.parent == settings.data_dir


def test_settings_are_a_singleton():
    assert get_settings() is get_settings()


def test_engine_and_sessionmaker_construct_without_connecting():
    engine = get_engine()
    assert engine.dialect.name == "postgresql"
    assert get_sessionmaker() is not None


def test_rule_objects_construct():
    from trustagent.rules.checks import CheckContext, RulePolicy

    assert RulePolicy.from_settings().large_transaction_threshold > 0
    assert RulePolicy() == RulePolicy.from_settings()  # code defaults agree with config defaults
    assert CheckContext(invoice=None, supplier=None).history == []  # type: ignore[arg-type]


def test_llm_factory_constructs_configured_models_without_calling_them():
    import pytest

    from trustagent.llm.factory import LLMNotConfigured, get_chat_model, model_for

    for role in ("generator", "judge"):
        provider, model = model_for(role)
        try:
            llm = get_chat_model(role)
        except LLMNotConfigured:
            pytest.skip(f"no API key for {provider}")
        assert model in repr(llm) or getattr(llm, "model_name", None) == model or getattr(llm, "model", None) == model


def test_missing_key_is_a_clear_error():
    import pytest

    from trustagent.config import Settings
    from trustagent.llm.factory import LLMNotConfigured, get_chat_model

    with pytest.raises(LLMNotConfigured, match="ANTHROPIC_API_KEY"):
        get_chat_model(provider="anthropic", model="any", settings=Settings(_env_file=None, anthropic_api_key=None))
