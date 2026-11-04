# StandRig AI settings

Edit `.env` in this folder and fill in the values supplied by your AI provider:

```dotenv
BASE_URL=https://your-provider.example/v1
MODEL_NAME=your-model-name
API_KEY=your-api-key
```

Use the provider's API base URL and exact model ID. Keep `.env` private; the
repository's `.gitignore` excludes it. `.env.example` contains no credentials
and can be used as a template.

Run `node server.mjs` in this folder, then open http://127.0.0.1:5182/.
Keep StandRig running on http://127.0.0.1:5180/ with the desired model loaded.
Settings are read on each request, so editing `.env` does not require a restart.
Provider requests use the same Python environment and HTTP client as Ciel.
The base URL should point to an OpenAI-compatible API root (usually ending in
`/v1`), not the `/chat/completions` endpoint. The selected model must support
chat completions and tool calling. The AI can request StandRig MCP tools;
your prompt, operator instructions and requested tool results are sent to the
configured provider. API credentials are not sent to StandRig.

The client can inspect the model, run numeric QA and submit modeling transactions
with `commit:false`. It blocks saved rig edits because it cannot create or
visually inspect StandRig's required visual-reference sheets and review results.
It does not generate artwork. Complete that visual workflow in a capable client
before implementing a reviewed commit path.

The progress panel shows MCP tool calls as they happen. The page keeps the
conversation in server memory so you can respond with `continue`; it is lost on
server restart or New conversation. Each prompt stops at the requested stage.
Dry-run changes are discarded and do not appear in the StandRig preview.
