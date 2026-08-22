const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { spawn, spawnSync } = require('node:child_process');

const MONITOR_URL = 'http://127.0.0.1:17678/';
const STARTUP_TIMEOUT_MS = 15000;
const RESTART_DELAY_MS = 250;

function isRepoRoot(candidate, existsSync = fs.existsSync) {
  return Boolean(candidate) && existsSync(path.join(candidate, 'src', 'phone_harness', 'monitor_web.py'));
}

function resolveRepoRoot(moduleDir = __dirname, env = process.env, existsSync = fs.existsSync) {
  const candidates = [
    env.PHONE_HARNESS_ROOT,
    path.resolve(moduleDir, '..'),
    path.join(os.homedir(), 'Documents', 'Intelligence Works', 'products', 'phone-harness-windows'),
  ].filter(Boolean);
  const selected = candidates.find((candidate) => isRepoRoot(candidate, existsSync));
  if (!selected) {
    throw new Error('Phone Harness root not found. Set PHONE_HARNESS_ROOT to the repository/worktree path.');
  }
  return path.resolve(selected);
}

function resolvePython(repoRoot, env = process.env, existsSync = fs.existsSync) {
  const candidates = [
    env.PHONE_HARNESS_PYTHON,
    path.join(repoRoot, '.venv', 'Scripts', 'python.exe'),
    path.join(
      os.homedir(),
      'Documents',
      'Intelligence Works',
      'products',
      'phone-harness-windows',
      '.venv',
      'Scripts',
      'python.exe',
    ),
  ].filter(Boolean);

  return candidates.find((candidate) => existsSync(candidate)) || 'python.exe';
}

function buildEnvironment(repoRoot, env = process.env) {
  const src = path.join(repoRoot, 'src');
  return {
    ...env,
    PYTHONPATH: env.PYTHONPATH ? `${src}${path.delimiter}${env.PYTHONPATH}` : src,
  };
}

function httpOk(url, timeoutMs = 750) {
  return new Promise((resolve) => {
    const request = http.get(url, { timeout: timeoutMs }, (response) => {
      response.resume();
      resolve(response.statusCode === 200);
    });
    request.on('timeout', () => request.destroy());
    request.on('error', () => resolve(false));
  });
}

async function waitForHttp(url, timeoutMs = STARTUP_TIMEOUT_MS) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const remaining = deadline - Date.now();
    if (await httpOk(url, Math.min(750, remaining))) return;
    await new Promise((resolve) => setTimeout(resolve, Math.min(200, Math.max(0, deadline - Date.now()))));
  }
  throw new Error(`Monitor server did not become ready: ${url}`);
}

class MonitorServer {
  constructor(repoRoot, {
    url = MONITOR_URL,
    onExit = null,
    readyTimeoutMs = STARTUP_TIMEOUT_MS,
  } = {}) {
    this.repoRoot = repoRoot;
    this.url = url;
    this.onExit = onExit;
    this.readyTimeoutMs = readyTimeoutMs;
    this.child = null;
    this.external = false;
    this.recentOutput = [];
    this._starting = null;
    this._restarting = null;
    this._startInProgress = false;
    this._startContext = null;
  }

  get owned() {
    return Boolean(this.child && this.child.exitCode === null);
  }

  status() {
    return {
      mode: this.owned ? 'owned' : (this.external ? 'external' : 'stopped'),
      running: this.owned || this.external,
      owned: this.owned,
      external: this.external,
      pid: this.owned ? this.child.pid : null,
      url: this.url,
      recentOutput: [...this.recentOutput],
    };
  }

  start() {
    if (this._starting) return this._starting;
    const operation = this._doStart();
    let shared;
    shared = operation.finally(() => {
      if (this._starting === shared) this._starting = null;
    });
    this._starting = shared;
    return shared;
  }

  async _doStart() {
    if (this.owned) return 'owned';
    if (await httpOk(this.url)) {
      this.external = true;
      return 'external';
    }

    this.external = false;
    this.recentOutput = [];
    const python = resolvePython(this.repoRoot);
    let child;
    try {
      child = spawn(
        python,
        ['-m', 'phone_harness.monitor_web', '--bind', '0.0.0.0', '--port', '17678'],
        {
          cwd: this.repoRoot,
          env: buildEnvironment(this.repoRoot),
          windowsHide: true,
          stdio: ['ignore', 'pipe', 'pipe'],
        },
      );
    } catch (error) {
      throw new Error(`Monitor server failed to start: ${error.message}`);
    }

    const context = { child, cancelled: false, spawnFailed: false };
    this.child = child;
    this._startContext = context;
    this._startInProgress = true;

    const capture = (chunk) => {
      this.recentOutput.push(String(chunk).trim());
      this.recentOutput = this.recentOutput.filter(Boolean).slice(-12);
    };
    child.stdout.on('data', capture);
    child.stderr.on('data', capture);

    let rejectStartup;
    const startupFailure = new Promise((resolve, reject) => {
      rejectStartup = reject;
    });
    let startupFailureReported = false;
    const failStartup = (error) => {
      if (startupFailureReported) return;
      startupFailureReported = true;
      rejectStartup(error instanceof Error ? error : new Error(String(error)));
    };

    child.once('error', (error) => {
      capture(`spawn error: ${error.message}`);
      if (this._startContext !== context || this.child !== child) return;
      if (this._startInProgress) {
        context.spawnFailed = true;
        failStartup(new Error(`Monitor server failed to start: ${error.message}`));
        return;
      }
      this.child = null;
      if (typeof this.onExit === 'function') this.onExit({ code: null, signal: null, error: error.message });
    });

    child.once('exit', (code, signal) => {
      if (this.child !== child) return;
      if (this._startInProgress) {
        failStartup(new Error(`Monitor server did not become ready: ${this.url} (exit=${code ?? '-'} signal=${signal ?? '-'})`));
        return;
      }
      this.child = null;
      if (typeof this.onExit === 'function') this.onExit({ code, signal });
    });

    try {
      await Promise.race([waitForHttp(this.url, this.readyTimeoutMs), startupFailure]);
      if (context.cancelled || this.child !== child) {
        throw new Error('Monitor server start was cancelled.');
      }
      if (child.exitCode !== null) {
        throw new Error(`Monitor server did not become ready: ${this.url} (exit=${child.exitCode})`);
      }
      this._startInProgress = false;
      this._startContext = null;
      return 'owned';
    } catch (error) {
      if (this._startContext === context) this._startContext = null;
      this._startInProgress = false;
      if (this.child === child) {
        if (context.spawnFailed) this.child = null;
        else this.stop();
      }
      const detail = this.recentOutput.join('\n');
      throw new Error(detail ? `${error.message}\n\n${detail}` : error.message);
    }
  }

  stop() {
    if (this.external) {
      throw new Error('The monitor server was started outside this Electron app and will not be stopped.');
    }
    const child = this.child;
    this.child = null;
    if (this._startContext && this._startContext.child === child) this._startContext.cancelled = true;
    if (!child || child.exitCode !== null || child.pid == null) return;

    if (process.platform === 'win32') {
      spawnSync('taskkill', ['/PID', String(child.pid), '/T', '/F'], {
        windowsHide: true,
        stdio: 'ignore',
      });
      return;
    }
    child.kill('SIGTERM');
  }

  restart() {
    if (this.external) {
      return Promise.reject(new Error('The monitor server was started outside this Electron app. Close that server first.'));
    }
    if (this._restarting) return this._restarting;
    let shared;
    shared = (async () => {
      try {
        if (this._starting) {
          try {
            await this._starting;
          } catch {
            // A restart is still allowed to make a fresh startup attempt.
          }
        }
        this.stop();
        await new Promise((resolve) => setTimeout(resolve, RESTART_DELAY_MS));
        return this.start();
      } finally {
        if (this._restarting === shared) this._restarting = null;
      }
    })();
    this._restarting = shared;
    return shared;
  }
}

module.exports = {
  MONITOR_URL,
  MonitorServer,
  buildEnvironment,
  httpOk,
  isRepoRoot,
  resolvePython,
  resolveRepoRoot,
  waitForHttp,
};
