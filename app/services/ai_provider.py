from __future__ import annotations

from typing import Optional

from openai import AsyncOpenAI


class AIProvider:
    def __init__(self, api_key: str, base_url: str, model: str):
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    async def generate(self, system_prompt: str, user_text: str) -> str:
        """
        Возвращает строку. Если модель вернула пусто — вернём понятную ошибку.
        """
        resp = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
            # для GPT-5 корректнее max_completion_tokens
            max_completion_tokens=1500,
            temperature=0.7,
        )

        content: Optional[str] = None
        if resp and resp.choices:
            msg = resp.choices[0].message
            content = (msg.content or "").strip() if msg else ""

        if not content:
            return "AI вернул пустой ответ. Попробуй ещё раз (или чуть позже)."

        return content
