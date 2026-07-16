import metergraph
from google import genai

metergraph.init(environment="example")
client = metergraph.wrap(genai.Client())


@metergraph.track
def haiku_about(topic: str) -> str:
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=f"Write a haiku about {topic}.",
    )
    return response.text


@metergraph.track("haiku.stream")
def haiku_stream(topic: str) -> None:
    for chunk in client.models.generate_content_stream(
        model="gemini-2.5-flash",
        contents=f"Write a haiku about {topic}.",
    ):
        print(chunk.text or "", end="", flush=True)
    print()


with metergraph.route("haiku-writer"):
    print(haiku_about("metered clouds"))
    haiku_stream("token budgets")
metergraph.flush()
metergraph.shutdown()
