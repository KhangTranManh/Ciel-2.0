import { readFile } from 'node:fs/promises';
import { runAgent } from './agent.mjs';

const values = {};
for (const line of (await readFile(new URL('.env', import.meta.url), 'utf8')).split(/\r?\n/)) {
  const match = line.match(/^\s*([A-Za-z_]\w*)\s*=\s*(.*?)\s*$/);
  if (match) values[match[1]] = match[2].replace(/^(["'])(.*)\1$/, '$2');
}
try {
  const endpoint = `${new URL(values.BASE_URL).href.replace(/\/+$/, '')}/chat/completions`;
  const result = await runAgent({ endpoint, model: values.MODEL_NAME, apiKey: values.API_KEY, prompt: 'Use standrig_context and state the number of bindings.', onProgress: message => console.log(message) });
  console.log('Tools:', result.activity.join(', '));
} catch (error) {
  console.log('Error type:', error.name);
  console.log('Error message:', error.message);
  if (error.cause?.code) console.log('Cause code:', error.cause.code);
}
