import Anthropic from "@anthropic-ai/sdk";
import * as mg from "metergraph";

mg.init({ environment: "example" });
const client = mg.wrap(new Anthropic());

const haikuAbout = mg.track("haiku.write", async (topic) => {
  const response = await client.messages.create({
    model: "claude-haiku-4-5",
    max_tokens: 100,
    messages: [{ role: "user", content: `Write a haiku about ${topic}.` }],
  });
  return response.content[0].text;
});

await mg.route("haiku-writer", async () => {
  console.log(await haikuAbout("metered clouds"));
});
await mg.flush();
await mg.shutdown();
