const http = require('http');

const PORT = 3000;
const OLLAMA_URL = 'http://localhost:11434/api/generate';
const ROUTER_MODEL = 'fauxpaslife/arch-router:1.5b';

const ROUTES = [
  { name: 'simple', description: 'Simple tasks, quick factual questions, basic formatting, grammar checks, summarization, or simple text generation with no complex logic.' },
  { name: 'medium', description: 'Standard programming tasks, data extraction and processing, intermediate reasoning, detailed explanations, or content generation requiring moderate context.' },
  { name: 'complex', description: 'Highly complex logical reasoning, advanced coding architectures, multi-step planning, intricate math, deep analytical thinking, or nuanced creative synthesis.' },
];

function buildRouterPrompt(messages) {
  return `<routes>\n${JSON.stringify(ROUTES)}\n</routes>\n\n<conversation>\n${JSON.stringify(messages)}\n</conversation>\n\nOutput the route name in JSON format: {"route": "route_name"}`;
}

function extractRoute(text) {
  const match = text.match(/"route"\s*:\s*['"]([^'"]+)['"]/) || text.match(/'route'\s*:\s*['"]([^'"]+)['"]/);
  const route = match ? match[1] : null;
  return ROUTES.some((r) => r.name === route) ? route : null;
}

async function classifyModelUncached(messages) {
  const prompt = buildRouterPrompt(messages);

  const ollamaRes = await fetch(OLLAMA_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model: ROUTER_MODEL, prompt, stream: false }),
  });

  if (!ollamaRes.ok) {
    throw new Error(`Ollama request failed: ${ollamaRes.status} ${ollamaRes.statusText}`);
  }

  const data = await ollamaRes.json();
  console.log('--- Ollama raw response ---');
  console.log(data.response);

  const route = extractRoute(data.response);
  if (!route) {
    throw new Error(`Could not parse a valid route from Ollama response: ${data.response}`);
  }

  return route;
}

// Portkey calls /check-complex and /check-medium separately per request; cache
// the classification so the second call for the same prompt is instant.
const complexityCache = new Map();
const CACHE_MAX_SIZE = 500;

async function classifyModel(messages) {
  const cacheKey = JSON.stringify(messages);

  if (complexityCache.has(cacheKey)) {
    console.log('--- Complexity cache hit ---');
    return complexityCache.get(cacheKey);
  }

  const complexity = await classifyModelUncached(messages);

  if (complexityCache.size >= CACHE_MAX_SIZE) {
    complexityCache.delete(complexityCache.keys().next().value);
  }
  complexityCache.set(cacheKey, complexity);

  return complexity;
}

function sendJson(res, statusCode, payload) {
  console.log('--- Response ---');
  console.log(JSON.stringify(payload, null, 2));
  res.writeHead(statusCode, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(payload));
}

async function handleComplexityGate(res, body, targetComplexity) {
  const messages = body.request && body.request.json && body.request.json.messages;

  if (!messages) {
    sendJson(res, 400, { verdict: false, error: 'No messages found in request body' });
    return;
  }

  try {
    const complexity = await classifyModel(messages);
    console.log(`--- Classified complexity: ${complexity} (target: ${targetComplexity}) ---`);
    sendJson(res, 200, { verdict: complexity === targetComplexity });
  } catch (err) {
    console.error('Model classification failed:', err.message);
    sendJson(res, 500, { verdict: false, error: 'Model classification failed' });
  }
}

const ROUTE_HANDLERS = {
  '/check-complex': (res, body) => handleComplexityGate(res, body, 'complex'),
  '/check-medium': (res, body) => handleComplexityGate(res, body, 'medium'),
};

const server = http.createServer((req, res) => {
  let rawBody = '';

  req.on('data', (chunk) => {
    rawBody += chunk;
  });

  req.on('end', async () => {
    console.log('\n=== Incoming Request ===');
    console.log(`${req.method} ${req.url}`);

    console.log('--- Headers ---');
    console.log(JSON.stringify(req.headers, null, 2));

    console.log('--- Body ---');
    console.log(rawBody);

    const { pathname } = new URL(req.url, `http://${req.headers.host}`);

    const handler = ROUTE_HANDLERS[pathname];
    if (!handler) {
      sendJson(res, 404, { verdict: false, error: `Unknown route: ${pathname}` });
      return;
    }

    let body;
    try {
      body = rawBody ? JSON.parse(rawBody) : {};
    } catch (err) {
      console.error('Failed to parse JSON body:', err.message);
      sendJson(res, 400, { verdict: false, error: 'Invalid JSON body' });
      return;
    }

    await handler(res, body);
  });
});

server.listen(PORT, () => {
  console.log(`Guardrail server listening on port ${PORT}`);
});
