'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { installSeams, makeChildProcessFake, makeHttpFake, waitFor } = require('./_harness.cjs');

const childProcess = makeChildProcessFake();
const http = makeHttpFake();
const restore = installSeams({ childProcess, http });
const { MonitorServer } = require('../server-process.cjs');

test.after(restore);
test.beforeEach(() => {
  childProcess.reset();
  http.reset();
});

function plan(statuses, fallback) {
  http.state.plan = statuses;
  http.state.fallback = fallback;
}

test('spawn failure rejects promptly without crash callback', async () => {
  plan([503], null);
  childProcess.state.spawnError = 'spawn python.exe ENOENT';
  const exits = [];
  const server = new MonitorServer('C:\\repo', { onExit: (info) => exits.push(info), readyTimeoutMs: 500 });

  await assert.rejects(server.start(), /ENOENT/);
  assert.equal(exits.length, 0);
  assert.equal(server.child, null);
  assert.equal(server.owned, false);
});

test('child exit during startup is a bounded startup failure', async () => {
  plan([503], null);
  const exits = [];
  const server = new MonitorServer('C:\\repo', { onExit: (info) => exits.push(info), readyTimeoutMs: 500 });
  const starting = server.start();
  await waitFor(() => childProcess.state.calls.length === 1);
  const child = childProcess.state.calls[0].child;
  child.stderr.emit('data', Buffer.from('ImportError: no module'));
  child.exit(1);

  await assert.rejects(starting, /did not become ready[\s\S]*ImportError/);
  assert.equal(exits.length, 0);
  assert.equal(server.owned, false);
});

test('startup timeout includes captured diagnostics and cleans up the exact child', async () => {
  plan([503], null);
  const server = new MonitorServer('C:\\repo', { readyTimeoutMs: 120 });
  const starting = server.start();
  await waitFor(() => childProcess.state.calls.length === 1);
  const child = childProcess.state.calls[0].child;
  child.stderr.emit('data', Buffer.from('Traceback: boom'));

  await assert.rejects(starting, /did not become ready[\s\S]*Traceback: boom/);
  assert.equal(server.owned, false);
  if (process.platform === 'win32') {
    assert.equal(childProcess.state.terminate.args[1], String(child.pid));
  } else {
    assert.equal(child.killSignal, 'SIGTERM');
  }
});

test('a retry succeeds after a bounded startup failure', async () => {
  plan([503], null);
  const server = new MonitorServer('C:\\repo', { readyTimeoutMs: 120 });
  await assert.rejects(server.start(), /did not become ready/);

  plan([503], 200);
  assert.equal(await server.start(), 'owned');
  assert.equal(childProcess.state.calls.length, 2);
});

test('concurrent starts share one in-flight promise', async () => {
  plan([503], 200);
  const server = new MonitorServer('C:\\repo');
  const first = server.start();
  const second = server.start();

  assert.equal(first, second);
  assert.equal(await first, 'owned');
  assert.equal(childProcess.state.calls.length, 1);
});

test('concurrent restarts share one in-flight promise', async () => {
  plan([503], 200);
  const server = new MonitorServer('C:\\repo');
  await server.start();
  plan([503], 503);
  const first = server.restart();
  const second = server.restart();

  assert.equal(first, second);
  await waitFor(() => childProcess.state.calls.length === 2);
  http.state.fallback = 200;
  assert.equal(await first, 'owned');
});

test('external ownership is read-only and rejects stop/restart', async () => {
  plan([200], 200);
  const server = new MonitorServer('C:\\repo');
  assert.equal(await server.start(), 'external');
  assert.equal(server.owned, false);
  assert.throws(() => server.stop(), /started outside this Electron app/);
  await assert.rejects(server.restart(), /started outside this Electron app/);
  assert.equal(childProcess.state.calls.length, 0);
});

test('intentional stop stays silent and does not report late exit', async () => {
  plan([503], 200);
  const exits = [];
  const server = new MonitorServer('C:\\repo', { onExit: (info) => exits.push(info) });
  await server.start();
  const child = server.child;
  server.stop();
  child.exit(0);

  assert.equal(server.owned, false);
  assert.equal(exits.length, 0);
});

test('intentional restart stays silent while a late old child exit is ignored', async () => {
  plan([503], 200);
  const exits = [];
  const server = new MonitorServer('C:\\repo', { onExit: (info) => exits.push(info) });
  await server.start();
  const first = server.child;
  plan([503], 503);
  const restarting = server.restart();
  await waitFor(() => childProcess.state.calls.length === 2);
  http.state.fallback = 200;
  assert.equal(await restarting, 'owned');
  first.exit(0);
  assert.equal(exits.length, 0);
});

test('real post-ready crash invokes the exit callback once', async () => {
  plan([503], 200);
  const exits = [];
  const server = new MonitorServer('C:\\repo', { onExit: (info) => exits.push(info) });
  await server.start();
  server.child.exit(3);

  assert.deepEqual(exits, [{ code: 3, signal: null }]);
  assert.equal(server.owned, false);
});

test('cleanup skips taskkill when a child has no valid PID', async () => {
  plan([503], null);
  const server = new MonitorServer('C:\\repo', { readyTimeoutMs: 120 });
  const starting = server.start();
  await waitFor(() => childProcess.state.calls.length === 1);
  childProcess.state.calls[0].child.pid = undefined;

  await assert.rejects(starting, /did not become ready/);
  assert.equal(childProcess.state.terminate, null);
});
