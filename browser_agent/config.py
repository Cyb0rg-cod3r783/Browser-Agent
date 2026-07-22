"""
config.py — Configuration loaded from .env file.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from the project directory
load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=False)


class Config:
    GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")
    DB_PATH: str = os.environ.get("DB_PATH", "./browser_agent.db")
    SCREENSHOTS_DIR: str = os.environ.get("SCREENSHOTS_DIR", "./screenshots")
    REPORTS_DIR: str = os.environ.get("REPORTS_DIR", "./reports")
    LLM_CACHE_DIR: str = os.environ.get("LLM_CACHE_DIR", "./llm_cache")
    GROQ_SMART_MODEL: str = os.environ.get("GROQ_SMART_MODEL", "qwen/qwen3-32b")
    GROQ_FAST_MODEL: str = os.environ.get("GROQ_FAST_MODEL", "llama-3.1-8b-instant")
    MAX_LLM_RETRIES: int = int(os.environ.get("MAX_LLM_RETRIES", "3"))
    LOCATOR_TIMEOUT_MS: int = int(os.environ.get("LOCATOR_TIMEOUT_MS", "3000"))
    NAVIGATION_TIMEOUT_MS: int = int(os.environ.get("NAVIGATION_TIMEOUT_MS", "60000"))
    PARALLEL_TESTS: int = int(os.environ.get("PARALLEL_TESTS", "4"))


config = Config()
