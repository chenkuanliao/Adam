import json
from collections.abc import AsyncIterator

import httpx

from ..schemas import ChatTurnIn


class OpenCodeGeminiProvider:
    """OpenCode Zen adapter for models exposed through the Gemini protocol."""

    def __init__(self, api_key: str, model: str, base_url: str) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")

    async def stream_answer(self, question: str, selected_text: str, page: int | None, history: list[ChatTurnIn]) -> AsyncIterator[str]:
        def user_prompt(turn_question: str, turn_selection: str, turn_page: int | None) -> str:
            if not turn_selection:
                return f"The user is asking a follow-up about the research paper. Use the preceding conversation as context.\n\nQuestion: {turn_question}"
            location = f"page {turn_page}" if turn_page else "one or more pages"
            return (
                "The user is reading a research paper. Answer using the exact selected passage below. "
                "Be precise, distinguish the paper's claim from your interpretation, and say when the passage alone is insufficient.\n\n"
                f"Selection ({location}):\n<selection>\n{turn_selection}\n</selection>\n\nQuestion: {turn_question}"
            )

        prompt = user_prompt(question, selected_text, page)
        url = f"{self.base_url}/models/{self.model}:streamGenerateContent"
        contents = []
        for turn in history[-10:]:
            contents.extend([
                {"role": "user", "parts": [{"text": user_prompt(turn.question, turn.selected_text, turn.page)}]},
                {"role": "model", "parts": [{"text": turn.answer}]},
            ])
        contents.append({"role": "user", "parts": [{"text": prompt}]})
        payload = {
            "contents": contents,
            "generationConfig": {"temperature": 0.2},
        }
        timeout = httpx.Timeout(connect=15, read=180, write=30, pool=15)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                url,
                params={"alt": "sse"},
                headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
                json=payload,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    event = json.loads(line[6:])
                    candidates = event.get("candidates") or []
                    if not candidates:
                        continue
                    parts = candidates[0].get("content", {}).get("parts", [])
                    for part in parts:
                        if text := part.get("text"):
                            yield text


def sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
