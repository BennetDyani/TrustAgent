"""All settings in one place.

Every path, model name, threshold and dimension is read from here, so
ingestion and querying can never disagree about which embedding model or
index directory is in use (see DECISIONS.md, ADR-004).
"""

from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]

Provider = Literal["gemini", "groq", "openai", "anthropic"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Database -----------------------------------------------------------
    database_url: str = "postgresql+psycopg://trustagent:trustagent@localhost:5433/trustagent"
    test_database_url: str = "postgresql+psycopg://trustagent:trustagent@localhost:5433/trustagent_test"

    # --- LLM providers ------------------------------------------------------
    # Generator model. Model IDs checked against provider docs (2026-09-30).
    llm_provider: Provider = "gemini"
    llm_model: str = "gemini-3.5-flash-lite"
    llm_temperature: float = 0.0
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 3

    # The LLM-as-judge must use a different provider from the generator.
    judge_provider: Provider = "groq"
    judge_model: str = "openai/gpt-oss-120b"

    google_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None

    # --- Embeddings (one model for ingestion AND querying) -----------------
    embedding_model: str = "gemini-embedding-001"
    # Must equal the pgvector column dimension; checked at start-up.
    embedding_dim: int = 768

    # --- Business rules (POL-001..POL-004) ----------------------------------
    large_transaction_threshold: Decimal = Decimal("100000")  # POL-002
    threshold_avoidance_band: Decimal = Decimal("0.05")
    pattern_multiplier: Decimal = Decimal("3")  # POL-003
    min_history_for_pattern: int = 3
    holder_match_threshold: float = 0.5
    supplier_match_threshold: float = 0.5
    contract_rate_tolerance: Decimal = Decimal("0.02")  # 2% over agreed rate

    # --- RAG -----------------------------------------------------------------
    chunk_size: int = 800
    chunk_overlap: int = 120
    rrf_k: int = 60
    retrieval_top_k: int = 5

    # --- Agent ---------------------------------------------------------------
    deep_dive_recursion_limit: int = 8

    # --- Paths -----------------------------------------------------------------
    data_dir: Path = PROJECT_ROOT / "data"

    @property
    def contracts_dir(self) -> Path:
        return self.data_dir / "contracts"

    @property
    def policies_dir(self) -> Path:
        return self.data_dir / "policies"

    @property
    def invoices_dir(self) -> Path:
        return self.data_dir / "invoices"

    @property
    def psycopg_conninfo(self) -> str:
        """Plain libpq URL for psycopg / the LangGraph checkpointer (no SQLAlchemy driver tag)."""
        return self.database_url.replace("postgresql+psycopg://", "postgresql://", 1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
