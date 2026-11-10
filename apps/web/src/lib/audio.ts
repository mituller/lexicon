// Pronunciation audio (Amazon Polly via the API), cached per word for the app's lifetime.

import { getTransport } from "./transport";

const cache = new Map<string, Promise<string>>();

export async function playWord(word: string): Promise<void> {
  let url = cache.get(word);
  if (!url) {
    url = getTransport()
      .pronounce(word)
      .then(({ audio, format }) => `data:audio/${format === "mp3" ? "mpeg" : format};base64,${audio}`);
    cache.set(word, url);
    url.catch(() => cache.delete(word));
  }
  await new Audio(await url).play();
}
