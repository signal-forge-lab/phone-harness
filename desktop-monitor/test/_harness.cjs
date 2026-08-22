'use strict';

const { EventEmitter } = require('node:events');
const Module = require('node:module');
const path = require('node:path');

const SERVER_MODULE = path.join(__dirname, '..', 'server-process.cjs');
const realLoad = Module._load;

class FakeChild extends EventEmitter {
  constructor(pid) {
    super();
    this.pid = pid;
    this.exitCode = null;
    this.killSignal = null;
    this.stdout = new EventEmitter();
    this.stderr = new EventEmitter();
  }

  kill(signal) {
    this.killSignal = signal;
  }

  exit(code, signal = null) {
    this.exitCode = code;
    this.emit('exit', code, signal);
  }
}

function makeChildProcessFake() {
  const state = { calls: [], terminate: null, spawnError: null, pidSeed: 4000 };
  const fake = {
    spawn(command, args, options) {
      const child = new FakeChild(state.pidSeed + state.calls.length);
      state.calls.push({ command, args, options, child });
      if (state.spawnError) process.nextTick(() => child.emit('error', new Error(state.spawnError)));
      return child;
    },
    spawnSync(command, args, options) {
      state.terminate = { command, args, options };
      return { status: 0 };
    },
    reset() {
      state.calls = [];
      state.terminate = null;
      state.spawnError = null;
    },
    state,
  };
  return fake;
}

function makeHttpFake() {
  const state = { plan: [], fallback: 200, calls: 0 };
  const fake = {
    get(url, options, callback) {
      const status = state.plan.length ? state.plan.shift() : state.fallback;
      state.calls += 1;
      const request = new EventEmitter();
      process.nextTick(() => {
        if (typeof status === 'number') callback({ statusCode: status, resume() {} });
        else request.emit('error', new Error('ECONNREFUSED'));
      });
      return request;
    },
    reset() {
      state.plan = [];
      state.fallback = 200;
      state.calls = 0;
    },
    state,
  };
  return fake;
}

function installSeams({ childProcess, http }) {
  delete require.cache[SERVER_MODULE];
  Module._load = (request, parent, isMain) => {
    if (parent?.filename === SERVER_MODULE && request === 'node:child_process') return childProcess;
    if (parent?.filename === SERVER_MODULE && request === 'node:http') return http;
    return realLoad(request, parent, isMain);
  };
  return () => {
    Module._load = realLoad;
    delete require.cache[SERVER_MODULE];
  };
}

async function waitFor(predicate, timeoutMs = 3000, intervalMs = 10) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error('waitFor timed out');
}

module.exports = { installSeams, makeChildProcessFake, makeHttpFake, waitFor };
