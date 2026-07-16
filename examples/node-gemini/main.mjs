import { GoogleGenAI } from "@google/genai";
import * as mg from "metergraph";

mg.init({ environment: "example" });
const client = mg.wrap(new GoogleGenAI({}));

const haikuAbout = mg.track("haiku.write", async (topic) => {
  const response = await client.models.generateContent({
    model: "gemini-2.5-flash",
    contents: `Write a haiku about ${topic}.`,
  });
  return response.text;
});

const haikuStream = mg.track("haiku.stream", async (topic) => {
  const stream = await client.models.generateContentStream({
    model: "gemini-2.5-flash",
    contents: `Write a haiku about ${topic}.`,
  });
  for await (const chunk of stream) process.stdout.write(chunk.text ?? "");
  process.stdout.write("\n");
});

await mg.route("haiku-writer", async () => {
  console.log(await haikuAbout("metered clouds"));
  await haikuStream("token budgets");
});
await mg.flush();
await mg.shutdown();
