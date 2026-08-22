const endpoint = process.argv[2] || 'http://127.0.0.1:9223';
const monitorUrl = 'http://127.0.0.1:17678/';

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function monitorHttpOk() {
  try {
    const response = await fetch(monitorUrl, { signal: AbortSignal.timeout(1200) });
    await response.arrayBuffer();
    return response.status === 200;
  } catch {
    return false;
  }
}

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

async function evaluate(expression, awaitPromise = true) {
  const { socket, call } = await connectPage();
  try {
    const response = await call('Runtime.evaluate', { expression, awaitPromise, returnByValue: true });
    return response.result?.value;
  } finally {
    socket.close();
  }
}

const status = () => evaluate('window.phoneHarnessDesktop.getStatus()');

async function invokeMonitor(action) {
  try {
    await evaluate(`window.phoneHarnessDesktop.monitor.${action}()`, false);
  } catch {
    // Start/restart reload the renderer; the old DevTools execution context can
    // disappear before returning even though the operation succeeded.
  }
}

async function main() {
  const initial = await status();
  if (!initial?.monitor?.owned || !initial.monitor.pid) throw new Error('Monitor is not Electron-owned before lifecycle smoke');

  await invokeMonitor('restart');
  await sleep(2600);
  const restarted = await status();
  if (!restarted?.monitor?.owned || !restarted.monitor.pid) throw new Error('Restart did not restore an owned monitor');
  if (restarted.monitor.pid === initial.monitor.pid) throw new Error('Restart kept the same monitor PID');
  if (!(await monitorHttpOk())) throw new Error('Monitor HTTP failed after restart');

  await invokeMonitor('stop');
  await sleep(1200);
  const stopped = await status();
  if (stopped?.monitor?.running) throw new Error('Stop left the monitor running');
  if (await monitorHttpOk()) throw new Error('Monitor HTTP still answered after stop');

  await invokeMonitor('start');
  await sleep(2600);
  const started = await status();
  if (!started?.monitor?.owned || !started.monitor.pid) throw new Error('Start did not restore an owned monitor');
  if (!(await monitorHttpOk())) throw new Error('Monitor HTTP failed after start');

  console.log(JSON.stringify({
    initialPid: initial.monitor.pid,
    restartedPid: restarted.monitor.pid,
    stopped: stopped.monitor,
    startedPid: started.monitor.pid,
    recentOutput: started.monitor.recentOutput,
  }, null, 2));
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
