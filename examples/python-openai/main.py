import metergraph
from openai import OpenAI

metergraph.init(environment="example")
client = metergraph.wrap(OpenAI())


@metergraph.track
def haiku_about(topic: str) -> str:
    response = client.chat.completions.create(
        model="gpt-5.6-luna",
        messages=[{"role": "user", "content": f"Write a haiku about {topic}."}],
    )
    return response.choices[0].message.content


with metergraph.route("haiku-writer"):
    print(haiku_about("metered clouds"))
metergraph.flush()
metergraph.shutdown()
