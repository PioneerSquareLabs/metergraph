import metergraph
from anthropic import Anthropic

metergraph.init(environment="example")
client = metergraph.wrap(Anthropic())


@metergraph.track
def haiku_about(topic: str) -> str:
    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=100,
        messages=[{"role": "user", "content": f"Write a haiku about {topic}."}],
    )
    return response.content[0].text


with metergraph.route("haiku-writer"):
    print(haiku_about("metered clouds"))
metergraph.flush()
metergraph.shutdown()
