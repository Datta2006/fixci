"""Claude prompt construction, invocation, and structured response parsing."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.models import Attempt, Confidence, Diagnosis, FailureContext

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a CI repair assistant. A GitHub Actions workflow has failed.

Rules you must follow:
- Make the MINIMAL change that fixes the failure.
- Never modify tests just to make them pass, unless the test itself is clearly wrong
  (e.g. it asserts a wrong expected value); say so explicitly if you do.
- Never modify CI configuration, workflow YAML, or anything under .github/.
- Never touch secrets, credentials, or environment files.
- Prefer fixing source/config over deleting or skipping checks.
- Output JSON ONLY. No markdown, no commentary, no code fences.

Return exactly this JSON shape:
{
  "root_cause": "<one sentence>",
  "explanation": "<why the patch fixes it>",
  "confidence": "low" | "medium" | "high",
  "files_changed": ["<path>", ...],
  "patch": "<unified diff that `git apply` can apply at the repo root>"
}
If you cannot determine a safe fix, return an empty string for "patch".
"""

JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
MAX_FILE_CHARS_IN_PROMPT = 6_000


def strip_code_fences(text: str) -> str:
    """Remove markdown code fences that models sometimes wrap JSON in."""
    match = JSON_FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


def parse_diagnosis(text: str) -> Diagnosis:
    """Parse and validate an LLM response into a ``Diagnosis``.

    Raises ``ValueError`` if the response is not valid JSON matching the schema.
    """
    cleaned = strip_code_fences(text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("LLM response contained no JSON object") from None
        try:
            data = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"LLM response was not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("LLM response JSON was not an object")
    # Normalise a couple of common deviations before strict validation.
    if isinstance(data.get("confidence"), str):
        data["confidence"] = data["confidence"].lower().strip()
    if isinstance(data.get("files_changed"), str):
        data["files_changed"] = [data["files_changed"]]
    if data.get("patch") is None:
        data["patch"] = ""
    return Diagnosis.model_validate(data)


def build_user_prompt(context: FailureContext, previous_attempt: Attempt | None = None) -> str:
    """Build the user prompt for a diagnosis (optionally with a retry context)."""
    parts = [
        f"Repository: {context.run.repo_full_name}",
        f"Branch: {context.run.head_branch}",
        f"Commit: {context.run.head_sha}",
        f"Failure classification: {context.failure_type.value}",
        f"Failing command: {context.failing_command or 'unknown'}",
    ]

    if context.workflow_yaml:
        parts.append(f"\n--- workflow YAML ---\n{context.workflow_yaml[:MAX_FILE_CHARS_IN_PROMPT]}")

    parts.append(f"\n--- commit diff ---\n{context.diff or '(empty)'}")

    if context.files:
        file_block = "\n\n".join(
            f"### {path}\n{content[:MAX_FILE_CHARS_IN_PROMPT]}"
            for path, content in context.files.items()
        )
        parts.append(f"\n--- relevant files ---\n{file_block}")

    parts.append(f"\n--- trimmed failed job log ---\n{context.trimmed_log}")

    if previous_attempt is not None:
        prev_patch = previous_attempt.diagnosis.patch if previous_attempt.diagnosis else "(none)"
        new_error = (
            previous_attempt.verification.output_tail
            if previous_attempt.verification
            else (previous_attempt.error or "(unknown)")
        )
        parts.append(
            "\n--- PREVIOUS ATTEMPT FAILED ---\n"
            f"Your previous patch was:\n{prev_patch}\n\n"
            f"Re-running the command still failed with:\n{new_error[:4000]}\n\n"
            "Produce a corrected patch. Do not repeat the same mistake."
        )

    parts.append(
        "\nRespond with JSON only, following the required schema"
        + (f" (classification: {context.failure_type.value})." if context.failure_type else ".")
    )
    return "\n".join(parts)


class LLMClient:
    """Thin wrapper around the Anthropic SDK with one JSON retry."""

    def __init__(
        self,
        api_key: str,
        model: str,
        client: Any | None = None,
        max_tokens: int = 4096,
        max_json_retries: int = 1,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.max_json_retries = max_json_retries
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client

    def _complete(self, user_prompt: str) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return "".join(
            block.text for block in response.content if getattr(block, "type", "text") == "text"
        )

    def diagnose(
        self, context: FailureContext, previous_attempt: Attempt | None = None
    ) -> Diagnosis:
        """Ask Claude for a diagnosis, retrying once on invalid JSON."""
        prompt = build_user_prompt(context, previous_attempt)
        last_error: Exception | None = None
        for _ in range(self.max_json_retries + 1):
            raw = self._complete(prompt)
            try:
                return parse_diagnosis(raw)
            except ValueError as exc:
                last_error = exc
                logger.warning("Invalid JSON from model, retrying: %s", exc)
                prompt = (
                    build_user_prompt(context, previous_attempt)
                    + "\n\nYour previous response was not valid JSON. "
                    "Respond with a single JSON object and nothing else."
                )
        raise ValueError(f"could not parse a diagnosis after retries: {last_error}")


__all__ = [
    "SYSTEM_PROMPT",
    "Confidence",
    "LLMClient",
    "build_user_prompt",
    "parse_diagnosis",
    "strip_code_fences",
]
