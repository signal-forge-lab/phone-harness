const test = require('node:test');
const assert = require('node:assert/strict');
const { DesktopControl, DEVELOPER_ACTIONS, HARNESS_ACTIONS, assertAction, commandNeedsShell } = require('../desktop-control.cjs');

test('desktop control accepts only allowlisted harness actions', () => {
  assert.equal(assertAction('restart', HARNESS_ACTIONS, 'harness'), 'restart');
  assert.throws(() => assertAction('shell', HARNESS_ACTIONS, 'harness'), /Unsupported/);
});

test('desktop control accepts only allowlisted developer actions', () => {
  assert.equal(assertAction('build', DEVELOPER_ACTIONS, 'developer'), 'build');
  assert.throws(() => assertAction('rm -rf', DEVELOPER_ACTIONS, 'developer'), /Unsupported/);
});

test('Windows command wrappers use the shell while executables do not', () => {
  assert.equal(commandNeedsShell('npm.cmd', 'win32'), true);
  assert.equal(commandNeedsShell('build.bat', 'win32'), true);
  assert.equal(commandNeedsShell('pwsh.exe', 'win32'), false);
  assert.equal(commandNeedsShell('npm.cmd', 'linux'), false);
});

test('concurrent harness operations run one at a time in call order', async () => {
  const control = new DesktopControl('repo');
  const calls = [];
  let active = 0;
  let maxActive = 0;
  control._script = async (name) => {
    active += 1;
    maxActive = Math.max(maxActive, active);
    calls.push(name);
    await new Promise((resolve) => setTimeout(resolve, 15));
    active -= 1;
    return { code: 0, output: `ok:${name}` };
  };
  const results = await Promise.all([
    control.harness('start'),
    control.harness('restart'),
    control.harness('stop'),
  ]);
  assert.equal(maxActive, 1, 'no two harness transitions may overlap');
  assert.ok(results.every((result) => result.code === 0));
  assert.deepEqual(calls, ['start', 'stop', 'start', 'stop']);
});

test('developer and harness actions share the same operation lock', async () => {
  const control = new DesktopControl('repo');
  const order = [];
  let active = 0;
  let maxActive = 0;
  control._build = async () => {
    active += 1;
    maxActive = Math.max(maxActive, active);
    order.push('build');
    await new Promise((resolve) => setTimeout(resolve, 15));
    active -= 1;
    return { code: 0, output: 'built' };
  };
  control._script = async (name) => {
    active += 1;
    maxActive = Math.max(maxActive, active);
    order.push(name);
    await new Promise((resolve) => setTimeout(resolve, 15));
    active -= 1;
    return { code: 0, output: `ok:${name}` };
  };
  const results = await Promise.all([
    control.developer('build'),
    control.harness('stop'),
  ]);
  assert.equal(maxActive, 1, 'build must not overlap a harness transition');
  assert.deepEqual(order, ['build', 'stop']);
  assert.ok(results.every((result) => result.code === 0));
});

test('build-and-restart completes under one lock without deadlocking', async () => {
  const control = new DesktopControl('repo');
  const scriptCalls = [];
  control._build = async () => ({ code: 0, output: 'built' });
  control._script = async (name) => {
    scriptCalls.push(name);
    return { code: 0, output: `ok:${name}` };
  };
  const result = await control.developer('build-and-restart');
  assert.deepEqual(result, { code: 0, output: 'Build and restart completed.' });
  assert.deepEqual(scriptCalls, ['stop', 'start']);
});

test('a failed operation releases the lock for the next queued operation', async () => {
  const control = new DesktopControl('repo');
  const calls = [];
  control._script = async (name) => {
    calls.push(name);
    await new Promise((resolve) => setTimeout(resolve, 10));
    if (name === 'stop' && calls.filter((n) => n === 'stop').length === 1) throw new Error('stop failed');
    return { code: 0, output: `ok:${name}` };
  };
  const [stopped, started] = await Promise.allSettled([
    control.harness('stop'),
    control.harness('start'),
  ]);
  assert.equal(stopped.status, 'rejected');
  assert.match(stopped.reason.message, /stop failed/);
  assert.equal(started.status, 'fulfilled');
  assert.equal(started.value.code, 0);
});
