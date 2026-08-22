const endpoint = process.argv[2] || 'http://127.0.0.1:9223';

async function connectPage() {
  const pages = await fetch(`${endpoint}/json/list`).then((response) => response.json());
  const page = pages.find((item) => item.type === 'page' && item.url.includes(':17678/'));
  if (!page?.webSocketDebuggerUrl) throw new Error('Phone Harness Monitor debug page not found');
  const socket = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, { once: true });
    socket.addEventListener('error', () => reject(new Error('DevTools WebSocket failed')), { once: true });
  });
  let nextId = 1;
  const pending = new Map();
  socket.addEventListener('message', (event) => {
    const message = JSON.parse(String(event.data));
    if (!message.id || !pending.has(message.id)) return;
    const item = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) item.reject(new Error(message.error.message)); else item.resolve(message.result);
  });
  const call = (method, params = {}) => new Promise((resolve, reject) => {
    const id = nextId++;
    pending.set(id, { resolve, reject });
    socket.send(JSON.stringify({ id, method, params }));
  });
  await call('Runtime.enable');
  return { socket, call };
}

async function evaluate(expression) {
  const { socket, call } = await connectPage();
  try {
    const response = await call('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    if (response.exceptionDetails) throw new Error(response.exceptionDetails.text || 'Renderer evaluation failed');
    return response.result?.value;
  } finally {
    socket.close();
  }
}

const status = () => evaluate('window.phoneHarnessDesktop.getStatus()');

async function main() {
  const before = await status();
  if (!before?.harness?.running || !before.harness.pid) throw new Error('Phone Harness is not running before lifecycle smoke');
  if (!before?.monitor?.running) throw new Error('Monitor is not running before lifecycle smoke');

  await evaluate('window.phoneHarnessDesktop.harness.stop()');
  const stopped = await status();
  if (stopped.harness.running) throw new Error('Phone Harness stop left MCP running');
  if (!stopped.monitor.running) throw new Error('Phone Harness stop also stopped Monitor');

  await evaluate('window.phoneHarnessDesktop.harness.start()');
  const started = await status();
  if (!started.harness.running || !started.harness.pid) throw new Error('Phone Harness start did not restore MCP');
  if (!started.monitor.running) throw new Error('Phone Harness start lost Monitor');
  if (started.harness.pid === before.harness.pid) throw new Error('Phone Harness start kept the old MCP PID');

  console.log(JSON.stringify({
    before: before.harness,
    stopped: stopped.harness,
    started: started.harness,
    monitor: started.monitor,
  }, null, 2));
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
