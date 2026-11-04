import assert from 'node:assert/strict';
import http from 'node:http';
import { runAgent } from './agent.mjs';

let requests = 0;
let sawContext = false;
let sawBlockedCommit = false;
const server = http.createServer(async (req, res) => {
  let raw = '';
  for await (const chunk of req) raw += chunk;
  const body = JSON.parse(raw);
  requests++;
  let message;
  if (requests === 1) {
    message = { role: 'assistant', content: null, tool_calls: [
      { id: 'context', type: 'function', function: { name: 'standrig_context', arguments: '{}' } },
      { id: 'commit', type: 'function', function: { name: 'standrig_modeling_transaction', arguments: '{"commit":true}' } },
    ] };
  } else {
    const context = body.messages.find(item => item.role === 'tool' && item.tool_call_id === 'context');
    const commit = body.messages.find(item => item.role === 'tool' && item.tool_call_id === 'commit');
    sawContext = context?.content.includes('ciel_upper_body_reference_repaired');
    sawBlockedCommit = commit?.content.includes('Saved rig changes are blocked');
    message = { role: 'assistant', content: 'Stage 1: inspected the model; waiting for continue.' };
  }
  res.writeHead(200, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify({ choices: [{ message }] }));
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
try {
  const events = [];
  const result = await runAgent({ endpoint: `http://127.0.0.1:${server.address().port}/chat/completions`, model: 'test', apiKey: 'test', prompt: 'Inspect this model.', onProgress: event => events.push(event) });
  assert.equal(requests, 2);
  assert.ok(sawContext, 'AI receives actual MCP context');
  assert.ok(sawBlockedCommit, 'committed rig writes are blocked');
  assert.ok(events.some(event => event.includes('standrig_context')));
  assert.ok(result.history.some(item => item.role === 'assistant'));
  console.log('Passed: MCP context, tool progress, history, and commit guard.');
} finally {
  server.close();
}
