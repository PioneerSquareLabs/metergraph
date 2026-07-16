import OpenAI from "openai";
import * as mg from "metergraph";

mg.init({ environment: "example" });
const client = mg.wrap(new OpenAI());

const haikuAbout = mg.track("haiku.write", async (topic) => {
  const response = await client.chat.completions.create({
    model: "gpt-5.6-luna",
    messages: [{ role: "user", content: `Write a haiku about ${topic}.` }],
  });
  return response.choices[0].message.content;
});

await mg.route("haiku-writer", async () => {
  console.log(await haikuAbout("metered clouds"));
});
await mg.flush();
await mg.shutdown();
