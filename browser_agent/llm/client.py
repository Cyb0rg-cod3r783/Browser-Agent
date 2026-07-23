"""
llm/client.py — Groq API wrapper with retry + rate limiting + caching.
Uses the groq Python library (AsyncGroq). Does NOT use anthropic or openai.
"""
import os
import json
import hashlib
from pathlib import Path
from dotenv import load_dotenv
from groq import AsyncGroq
import groq as groq_module
from tenacity import (
    retry, stop_after_attempt,
    wait_exponential, retry_if_exception_type
)
from llm.cache import LLMCache

# Load .env from the browser_agent package directory
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=False)


class LLMClient:
    def __init__(self, db_path: str = "./browser_agent.db"):
        self.client = AsyncGroq(api_key=os.environ.get("GROQ_API_KEY"))
        self.smart_model = os.environ.get(
            "GROQ_SMART_MODEL", "llama-3.3-70b-versatile"
        )
        self.fast_model = os.environ.get(
            "GROQ_FAST_MODEL", "llama-3.1-8b-instant"
        )
        self.cache = LLMCache(db_path=db_path)

    async def generate(
        self,
        prompt: str,
        model: str = "haiku",   # accepts "haiku" (fast) or "sonnet" (smart)
        expect_json: bool = True
    ) -> dict | str:

        # Map familiar names to actual Groq model strings
        actual_model = (
            self.fast_model if model == "haiku" else self.smart_model
        )

        # Check cache before calling API
        input_hash = hashlib.sha256(
            f"{actual_model}{prompt}".encode()
        ).hexdigest()
        cached = await self.cache.get(input_hash)
        if cached:
            return json.loads(cached) if expect_json else cached

        # Call Groq API
        response = await self._call_api(actual_model, prompt, expect_json)
        text = response.choices[0].message.content.strip()

        if expect_json:
            text = self._strip_json_fences(text)
            try:
                result = json.loads(text)
            except json.JSONDecodeError:
                # Second attempt: ask the model to fix its own output
                fix_prompt = (
                    f"The following is not valid JSON. "
                    f"Fix it and return ONLY valid JSON:\n{text}"
                )
                retry_response = await self._call_api(
                    actual_model, fix_prompt, expect_json=True
                )
                text = self._strip_json_fences(
                    retry_response.choices[0].message.content.strip()
                )
                result = json.loads(text)

            await self.cache.set(input_hash, json.dumps(result), actual_model)
            return result
        else:
            await self.cache.set(input_hash, text, actual_model)
            return text

    @retry(
        stop=stop_after_attempt(6),
        wait=wait_exponential(multiplier=2, min=4, max=65),
        retry=retry_if_exception_type((
            groq_module.RateLimitError,
            groq_module.APIStatusError,
            groq_module.APIConnectionError
        ))
    )
    async def _call_api(self, model: str, prompt: str, expect_json: bool):
        kwargs = dict(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a precise assistant. "
                        "When asked to return JSON, respond ONLY with "
                        "valid JSON — no preamble, no explanation, "
                        "no markdown code fences."
                    )
                },
                {"role": "user", "content": prompt}
            ],
            max_tokens=4096,
            temperature=0.1   # low temperature = more consistent JSON output
        )
        if expect_json:
            kwargs["response_format"] = {"type": "json_object"}
        return await self.client.chat.completions.create(**kwargs)

    @staticmethod
    def _strip_json_fences(text: str) -> str:
        """Remove markdown code fences if the model includes them anyway."""
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.startswith("```")]
            text = "\n".join(lines)
        return text.strip()
