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

async function main() {
  const before = await evaluate('window.phoneHarnessDesktop.getStatus()');
  if (!before?.harness?.running || !before.harness.pid) throw new Error('Phone Harness is not running before developer smoke');

  const build = await evaluate('window.phoneHarnessDesktop.developer.build()');
  if (build?.code !== 0) throw new Error(`Build returned ${build?.code}`);

  const buildRestart = await evaluate('window.phoneHarnessDesktop.developer.buildAndRestart()');
  if (buildRestart?.code !== 0) throw new Error(`Build & Restart returned ${buildRestart?.code}`);

  const after = await evaluate('window.phoneHarnessDesktop.getStatus()');
  if (!after?.harness?.running || !after.harness.pid) throw new Error('Build & Restart did not restore Phone Harness');
  if (after.harness.pid === before.harness.pid) throw new Error('Build & Restart kept the same MCP PID');
  if (!after?.monitor?.running) throw new Error('Build & Restart stopped Monitor');

  console.log(JSON.stringify({
    beforePid: before.harness.pid,
    build: { code: build.code, outputTail: String(build.output || '').slice(-800) },
    buildRestart,
    after: after.harness,
    monitor: after.monitor,
  }, null, 2));
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
