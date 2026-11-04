import http from 'node:http';
import dns from 'node:dns';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { runAgent } from './agent.mjs';
import { randomUUID } from 'node:crypto';

// The configured provider is reachable over IPv4 on this computer, while
// Node's default DNS ordering can try an unreachable IPv6 address first.
dns.setDefaultResultOrder('ipv4first');

const dir = path.dirname(fileURLToPath(import.meta.url));
const host = '127.0.0.1';
const port = 5182;
const standrig = 'http://127.0.0.1:5180';
const sessions = new Map();

async function config() {
  const text = await readFile(path.join(dir, '.env'), 'utf8');
  const values = {};
  for (const line of text.split(/\r?\n/)) {
    const match = line.match(/^\s*([A-Za-z_][\w]*)\s*=\s*(.*?)\s*$/);
    if (!match) continue;
    let value = match[2];
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) value = value.slice(1, -1);
    values[match[1]] = value.trim();
  }
  return values;
}

function reply(res, status, data) {
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' });
  res.end(JSON.stringify(data));
}

async function body(req) {
  let text = '';
  for await (const chunk of req) {
    text += chunk;
    if (Buffer.byteLength(text) > 32768) throw new Error('Prompt is too large.');
  }
  return JSON.parse(text);
}

async function context() {
  const response = await fetch(`${standrig}/api/context`, { signal: AbortSignal.timeout(5000) });
  if (!response.ok) throw new Error('StandRig is not responding on port 5180.');
  const result = await response.json();
  return { name: result.context.summary.name, counts: result.context.summary.counts, issues: result.context.unresolvedIssues };
}

http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, `http://${host}:${port}`);
    if (req.method === 'GET' && url.pathname === '/') {
      const html = await readFile(path.join(dir, 'index.html'));
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store', 'Content-Security-Policy': "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'", 'X-Content-Type-Options': 'nosniff' });
      return res.end(html);
    }
    if (req.method === 'GET' && url.pathname === '/api/status') {
      const cfg = await config();
      const missing = ['BASE_URL', 'MODEL_NAME', 'API_KEY'].filter(key => !cfg[key]);
      let model = null;
      try { model = await context(); } catch { /* report disconnected */ }
      return reply(res, 200, { configured: missing.length === 0, missing, standrigConnected: Boolean(model), model: model?.name ?? null });
    }
    if (req.method === 'POST' && url.pathname === '/api/prompt') {
      if (req.headers.origin !== `http://${host}:${port}` || !req.headers['content-type']?.startsWith('application/json')) return reply(res, 403, { error: 'Invalid request origin.' });
      const input = await body(req);
      if (typeof input.prompt !== 'string' || !input.prompt.trim() || input.prompt.length > 12000) return reply(res, 400, { error: 'Enter a prompt of up to 12,000 characters.' });
      const cfg = await config();
      const missing = ['BASE_URL', 'MODEL_NAME', 'API_KEY'].filter(key => !cfg[key]);
      if (missing.length) return reply(res, 400, { error: `Fill these fields in .env: ${missing.join(', ')}.` });
      const base = new URL(cfg.BASE_URL);
      if (base.protocol !== 'https:' && !(base.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(base.hostname))) return reply(res, 400, { error: 'BASE_URL must use HTTPS, or HTTP for a local provider.' });
      if (base.username || base.password || base.search || base.hash) return reply(res, 400, { error: 'BASE_URL cannot contain credentials, a query, or a fragment.' });
      const endpoint = `${base.href.replace(/\/+$/, '')}/chat/completions`;
      await context();
      const sessionId = typeof input.sessionId === 'string' && sessions.has(input.sessionId) ? input.sessionId : randomUUID();
      const session = sessions.get(sessionId) ?? { history: [], busy: false };
      if (session.busy) return reply(res, 409, { error: 'Wait for the current request to finish.' });
      session.busy = true;
      sessions.set(sessionId, session);
      res.writeHead(200, { 'Content-Type': 'text/event-stream; charset=utf-8', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' });
      const send = (event, data) => res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
      send('session', { sessionId });
      const heartbeat = setInterval(() => res.write(': working\n\n'), 15000);
      try {
        const result = await runAgent({ endpoint, model: cfg.MODEL_NAME, apiKey: cfg.API_KEY, prompt: input.prompt, history: session.history, onProgress: message => send('progress', { message }) });
        session.history = result.history.length <= 60 && JSON.stringify(result.history).length <= 500000 ? result.history : [];
        send('done', { answer: result.answer, activity: result.activity, historyReset: !session.history.length });
      } catch (error) {
        console.error('Agent failed:', error.name);
        send('error', { error: error.message.startsWith('AI provider returned HTTP ') || error.message.startsWith('Provider returned no ') || error.message.startsWith('The AI reached ') || error.message.startsWith('Could not connect to the AI provider') ? error.message : 'AI or MCP connection failed. Check the provider, its tool-calling support, and StandRig.' });
      } finally {
        clearInterval(heartbeat);
        session.busy = false;
        res.end();
      }
      return;
    }
    reply(res, 404, { error: 'Not found.' });
  } catch (error) {
    console.error('Request failed:', error.name);
    reply(res, 500, { error: 'Request failed. Check that StandRig is running and your provider settings are valid.' });
  }
}).listen(port, host, () => console.log(`StandRig AI prompt: http://${host}:${port}`));
