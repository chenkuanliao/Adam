import json
from collections.abc import AsyncIterator
from uuid import uuid4

import httpx

from ..schemas import ChatTurnIn, ContextImageIn

TITLE_MODEL = "gpt-5.6-luna"
TITLE_SYSTEM_PROMPT = (
    "Write a short, specific title for this research-paper chat. Return only the title, "
    "with no quotation marks, markdown, or ending punctuation. Use at most 8 words."
)


def request_headers(api_key: str, base_url: str, **extra: str) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": "Adam-Paper-Reader/0.1", **extra}
    if "opencode.ai/zen/" in base_url:
        headers["x-opencode-session"] = str(uuid4())
    return headers


def user_prompt(question: str, selection: str, page: int | None) -> str:
    if not selection:
        return f"Answer the user's question using the paper reference and conversation context.\n\nQuestion: {question}"
    location = f"page {page}" if page else "one or more pages"
    return ("The passage below is the user's explicit focus for this question. Prioritize it while using the rest of the paper when helpful. "
            "Selection does not imply that the user agrees with it. Be precise and distinguish the paper's claim from your interpretation.\n\n"
            f"Selection ({location}):\n<selection>\n{selection}\n</selection>\n\nQuestion: {question}")


def clean_title(value: str, fallback: str) -> str:
    title = value.strip().strip('"\'`').splitlines()[0].strip() if value.strip() else ""
    title = title.removesuffix(".").strip()
    return (title or fallback.strip() or "New chat")[:200]


async def generate_zen_title(api_key: str, transcript: str, fallback: str) -> str:
    """Generate a title through OpenCode Zen's Responses-compatible Luna model."""
    payload = {
        "model": TITLE_MODEL,
        "instructions": TITLE_SYSTEM_PROMPT,
        "input": transcript[:30000],
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=15, read=45, write=30, pool=15)) as client:
        response = await client.post(
            "https://opencode.ai/zen/v1/responses",
            headers=request_headers(api_key, "https://opencode.ai/zen/v1"),
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
    text = data.get("output_text", "")
    if not text:
        for item in data.get("output", []):
            for content in item.get("content", []):
                if content.get("type") in {"output_text", "text"}:
                    text += content.get("text", "")
    return clean_title(text, fallback)


class GoogleProvider:

    def __init__(self, api_key: str, model: str, base_url: str, system_prompt: str = "") -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.system_prompt = system_prompt

    async def stream_answer(self, question: str, selected_text: str, images: list[ContextImageIn], page: int | None, history: list[ChatTurnIn]) -> AsyncIterator[str]:
        def user_parts(turn_question: str, turn_selection: str, turn_images: list[ContextImageIn], turn_page: int | None) -> list[dict]:
            prompt = user_prompt(turn_question, turn_selection, turn_page)
            if turn_images:
                prompt += "\n\nThe attached image(s) are user-selected regions of the PDF. Treat them as primary context."
            parts: list[dict] = [{"text": prompt}]
            for image in turn_images:
                header, encoded = image.data_url.split(",", 1)
                mime_type = header[5:].split(";", 1)[0]
                parts.append({"inlineData": {"mimeType": mime_type, "data": encoded}})
            return parts

        url = f"{self.base_url}/models/{self.model}:streamGenerateContent"
        contents = []
        for turn in history[-10:]:
            contents.extend([
                {"role": "user", "parts": user_parts(turn.question, turn.selected_text, turn.images, turn.page)},
                {"role": "model", "parts": [{"text": turn.answer}]},
            ])
        contents.append({"role": "user", "parts": user_parts(question, selected_text, images, page)})
        payload = {
            "contents": contents,
            "generationConfig": {"temperature": 0.2},
        }
        if self.system_prompt:
            payload["systemInstruction"] = {"parts": [{"text": self.system_prompt}]}
        timeout = httpx.Timeout(connect=15, read=180, write=30, pool=15)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                url,
                params={"alt": "sse"},
                headers=request_headers(self.api_key, self.base_url, **{"x-goog-api-key": self.api_key}),
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


class OpenAICompatibleProvider:
    def __init__(self, api_key: str, model: str, base_url: str, system_prompt: str = "") -> None:
        self.api_key, self.model, self.base_url = api_key, model, base_url.rstrip("/")
        self.system_prompt = system_prompt

    def parts(self, question: str, selection: str, images: list[ContextImageIn], page: int | None) -> list[dict]:
        parts: list[dict] = [{"type": "text", "text": user_prompt(question, selection, page)}]
        parts.extend({"type": "image_url", "image_url": {"url": image.data_url}} for image in images)
        return parts

    async def stream_answer(self, question: str, selected_text: str, images: list[ContextImageIn], page: int | None, history: list[ChatTurnIn]) -> AsyncIterator[str]:
        messages = [{"role": "system", "content": self.system_prompt}] if self.system_prompt else []
        for turn in history[-10:]:
            messages.extend([{"role": "user", "content": self.parts(turn.question, turn.selected_text, turn.images, turn.page)}, {"role": "assistant", "content": turn.answer}])
        messages.append({"role": "user", "content": self.parts(question, selected_text, images, page)})
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=15, read=180, write=30, pool=15)) as client:
            async with client.stream("POST", f"{self.base_url}/chat/completions", headers=request_headers(self.api_key, self.base_url), json={"model": self.model, "messages": messages, "stream": True, "temperature": .2}) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: ") or line == "data: [DONE]": continue
                    event = json.loads(line[6:])
                    text = ((event.get("choices") or [{}])[0].get("delta") or {}).get("content")
                    if text: yield text


class OpenAIResponsesProvider:
    def __init__(self, api_key: str, model: str, base_url: str, system_prompt: str = "") -> None:
        self.api_key, self.model, self.base_url = api_key, model, base_url.rstrip("/")
        self.system_prompt = system_prompt

    def content(self, question: str, selection: str, images: list[ContextImageIn], page: int | None) -> list[dict]:
        content: list[dict] = [{"type": "input_text", "text": user_prompt(question, selection, page)}]
        content.extend({"type": "input_image", "image_url": image.data_url} for image in images)
        return content

    async def stream_answer(self, question: str, selected_text: str, images: list[ContextImageIn], page: int | None, history: list[ChatTurnIn]) -> AsyncIterator[str]:
        inputs = []
        for turn in history[-10:]:
            inputs.extend([{"role": "user", "content": self.content(turn.question, turn.selected_text, turn.images, turn.page)}, {"role": "assistant", "content": turn.answer}])
        inputs.append({"role": "user", "content": self.content(question, selected_text, images, page)})
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=15, read=180, write=30, pool=15)) as client:
            async with client.stream("POST", f"{self.base_url}/responses", headers=request_headers(self.api_key, self.base_url), json={"model": self.model, "instructions": self.system_prompt, "input": inputs, "stream": True}) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data: ") and line != "data: [DONE]":
                        event = json.loads(line[6:])
                        if event.get("type") == "response.output_text.delta" and event.get("delta"):
                            yield event["delta"]


class AnthropicProvider:
    def __init__(self, api_key: str, model: str, base_url: str = "https://api.anthropic.com/v1", system_prompt: str = "") -> None:
        self.api_key, self.model, self.base_url = api_key, model, base_url.rstrip("/")
        self.system_prompt = system_prompt

    def content(self, question: str, selection: str, images: list[ContextImageIn], page: int | None) -> list[dict]:
        content: list[dict] = [{"type": "text", "text": user_prompt(question, selection, page)}]
        for image in images:
            header, encoded = image.data_url.split(",", 1)
            content.append({"type": "image", "source": {"type": "base64", "media_type": header[5:].split(";", 1)[0], "data": encoded}})
        return content

    async def stream_answer(self, question: str, selected_text: str, images: list[ContextImageIn], page: int | None, history: list[ChatTurnIn]) -> AsyncIterator[str]:
        messages = []
        for turn in history[-10:]:
            messages.extend([{"role": "user", "content": self.content(turn.question, turn.selected_text, turn.images, turn.page)}, {"role": "assistant", "content": turn.answer}])
        messages.append({"role": "user", "content": self.content(question, selected_text, images, page)})
        headers = request_headers(self.api_key, self.base_url, **{"x-api-key": self.api_key, "anthropic-version": "2023-06-01"})
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=15, read=180, write=30, pool=15)) as client:
            async with client.stream("POST", f"{self.base_url}/messages", headers=headers, json={"model": self.model, "system": self.system_prompt, "messages": messages, "max_tokens": 4096, "stream": True, "temperature": .2}) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        event = json.loads(line[6:])
                        if event.get("type") == "content_block_delta" and event.get("delta", {}).get("type") == "text_delta":
                            yield event["delta"]["text"]


# Backwards-compatible name for existing imports.
OpenCodeGeminiProvider = GoogleProvider


def sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
