"""Offline stand-ins for the OpenAI, Anthropic, and Gemini clients.

They mimic just enough of each SDK's response shape for metergraph.wrap()
to capture usage, so examples and CI can run without API keys.
"""

from types import SimpleNamespace


class FakeOpenAI:
    def __init__(self):
        completions = SimpleNamespace(create=self._create)
        self.chat = SimpleNamespace(completions=completions)
        self.responses = None

    @staticmethod
    def _create(**kwargs):
        return SimpleNamespace(
            id="req_openai_1",
            usage=SimpleNamespace(
                prompt_tokens=1200,
                completion_tokens=180,
                prompt_tokens_details=SimpleNamespace(cached_tokens=400),
            ),
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="summary"),
                    finish_reason="stop",
                )
            ],
        )


class FakeAnthropic:
    def __init__(self):
        self.messages = SimpleNamespace(create=self._create)

    @staticmethod
    def _create(**kwargs):
        return SimpleNamespace(
            id="req_anthropic_1",
            usage=SimpleNamespace(
                input_tokens=900,
                output_tokens=220,
                cache_read_input_tokens=300,
                cache_creation_input_tokens=0,
            ),
            content=[SimpleNamespace(type="text", text="classified")],
            stop_reason="end_turn",
        )


class FakeGemini:
    def __init__(self):
        self.models = SimpleNamespace(
            generate_content=self._generate,
            generate_content_stream=self._generate_stream,
        )

    @staticmethod
    def _usage(output_tokens):
        return SimpleNamespace(
            prompt_token_count=700,
            candidates_token_count=output_tokens,
            cached_content_token_count=100,
            thoughts_token_count=40,
        )

    @classmethod
    def _generate(cls, **kwargs):
        return SimpleNamespace(
            response_id="req_gemini_1",
            text="extracted",
            usage_metadata=cls._usage(150),
        )

    @classmethod
    def _generate_stream(cls, **kwargs):
        for chunk in range(1, 4):
            yield SimpleNamespace(
                response_id="req_gemini_2",
                text="chunk",
                usage_metadata=cls._usage(50 * chunk),
            )
