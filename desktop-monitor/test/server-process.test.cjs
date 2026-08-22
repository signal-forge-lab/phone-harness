const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const assert = require('node:assert/strict');
const { buildEnvironment, resolvePython, resolveRepoRoot } = require('../server-process.cjs');

test('resolvePython prefers explicit PHONE_HARNESS_PYTHON', () => {
  const selected = resolvePython('C:\\repo', { PHONE_HARNESS_PYTHON: 'C:\\custom\\python.exe' }, () => true);
  assert.equal(selected, 'C:\\custom\\python.exe');
});

test('resolvePython uses repository venv when present', () => {
  const repoRoot = path.join('C:\\', 'repo');
  const expected = path.join(repoRoot, '.venv', 'Scripts', 'python.exe');
  const selected = resolvePython(repoRoot, {}, (candidate) => candidate === expected);
  assert.equal(selected, expected);
});

test('buildEnvironment prepends repository src to PYTHONPATH', () => {
  const repoRoot = path.join(os.tmpdir(), 'phone-harness');
  const env = buildEnvironment(repoRoot, { PYTHONPATH: 'existing' });
  assert.equal(env.PYTHONPATH, `${path.join(repoRoot, 'src')}${path.delimiter}existing`);
});

test('resolveRepoRoot prefers explicit PHONE_HARNESS_ROOT', () => {
  const selected = resolveRepoRoot('C:\\app', { PHONE_HARNESS_ROOT: 'C:\\repo' }, (candidate) => (
    candidate === path.join('C:\\repo', 'src', 'phone_harness', 'monitor_web.py')
  ));
  assert.equal(selected, path.resolve('C:\\repo'));
});

