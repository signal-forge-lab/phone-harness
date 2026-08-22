const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn, spawnSync } = require('node:child_process');

const HARNESS_ACTIONS = new Set(['start', 'stop', 'restart']);
const DEVELOPER_ACTIONS = new Set(['build', 'build-and-restart', 'build-exe']);

function readJson(filePath) {
  try { return JSON.parse(fs.readFileSync(filePath, 'utf8')); } catch { return {}; }
}

function readPid(filePath) {
  try {
    const value = Number.parseInt(fs.readFileSync(filePath, 'utf8').trim(), 10);
    return Number.isInteger(value) && value > 0 ? value : null;
  } catch {
    return null;
  }
}

function processExists(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try { process.kill(pid, 0); return true; } catch { return false; }
}

function processStartTime(pid) {
  if (!Number.isInteger(pid) || pid <= 0 || process.platform !== 'win32') return null;
  const script = `(Get-Process -Id ${pid} -ErrorAction SilentlyContinue).StartTime.ToUniversalTime().ToString('o')`;
  const result = spawnSync('powershell.exe', ['-NoProfile', '-Command', script], {
    windowsHide: true,
    encoding: 'utf8',
  });
  return result.status === 0 && result.stdout.trim() ? result.stdout.trim() : null;
}

function assertAction(action, allowed, kind) {
  if (!allowed.has(action)) throw new Error(`Unsupported ${kind} action: ${String(action)}`);
  return action;
}

function commandNeedsShell(command, platform = process.platform) {
  return platform === 'win32' && /\.(?:cmd|bat)$/i.test(String(command));
}

function runCapture(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: options.cwd,
      env: options.env || process.env,
      windowsHide: true,
      shell: commandNeedsShell(command),
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    const output = [];
    const capture = (chunk) => { const text = String(chunk).trim(); if (text) output.push(text); };
    child.stdout.on('data', capture);
    child.stderr.on('data', capture);
    child.once('error', reject);
    child.once('exit', (code, signal) => {
      const text = output.join('\n');
      if (code === 0) resolve({ code, signal, output: text });
      else reject(new Error(text || `${command} exited with ${code ?? signal ?? 'unknown status'}`));
    });
  });
}

class DesktopControl {
  constructor(repoRoot) {
    this.repoRoot = repoRoot;
    this.logs = [];
    this._operation = Promise.resolve();
  }

  // Shared in-memory conflict lock: Build, Build & Restart and Harness
  // transitions run one at a time, in call order, across this instance.
  async _withLock(task) {
    const previous = this._operation;
    let release;
    this._operation = new Promise((resolve) => { release = resolve; });
    await previous.catch(() => {});
    try {
      return await task();
    } finally {
      release();
    }
  }

  _record(label, output) {
    this.logs.push({ at: new Date().toISOString(), label, output: String(output || '') });
    this.logs = this.logs.slice(-40);
  }

  getLogs() { return [...this.logs]; }

  status() {
    const stateDir = path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local'), 'phone-harness-mcp');
    const config = readJson(path.join(stateDir, 'config.json'));
    const pid = readPid(path.join(stateDir, 'mcp.pid'));
    const running = processExists(pid);
    const startedAt = running ? processStartTime(pid) : null;
    return {
      running,
      pid: running ? pid : null,
      startedAt,
      uptimeSeconds: startedAt ? Math.max(0, Math.round((Date.now() - Date.parse(startedAt)) / 1000)) : null,
      transport: config.transport || null,
      mcpPort: Number(config.port || 17677),
    };
  }

  async _script(name) {
    const filePath = path.join(this.repoRoot, 'tools', name === 'start' ? 'start_phone_harness.ps1' : 'stop_phone_harness.ps1');
    if (!fs.existsSync(filePath)) throw new Error(`Phone Harness script not found: ${filePath}`);
    const args = ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', filePath];
    if (name === 'stop') args.push('-KeepMonitor', '-KeepTunneld');
    const result = await runCapture('pwsh.exe', args, { cwd: this.repoRoot });
    this._record(`harness:${name}`, result.output);
    return result;
  }

  async harness(action) {
    assertAction(action, HARNESS_ACTIONS, 'Phone Harness');
    return this._withLock(() => this._harness(action));
  }

  async _harness(action) {
    if (action === 'restart') {
      await this._script('stop');
      return this._script('start');
    }
    return this._script(action);
  }

  async _build() {
    const steps = [
      ['npm.cmd', ['run', 'build'], path.join(this.repoRoot, 'mcp-server')],
      ['npm.cmd', ['run', 'check'], path.join(this.repoRoot, 'desktop-monitor')],
    ];
    const outputs = [];
    for (const [command, args, cwd] of steps) {
      const result = await runCapture(command, args, { cwd });
      outputs.push(result.output);
    }
    const output = outputs.filter(Boolean).join('\n');
    this._record('developer:build', output);
    return { code: 0, output };
  }

  async developer(action) {
    assertAction(action, DEVELOPER_ACTIONS, 'developer');
    return this._withLock(() => this._developer(action));
  }

  async _developer(action) {
    if (action === 'build') return this._build();
    if (action === 'build-and-restart') {
      await this._build();
      await this._harness('restart');
      return { code: 0, output: 'Build and restart completed.' };
    }
    const result = await runCapture('npm.cmd', ['run', 'build:exe'], { cwd: path.join(this.repoRoot, 'desktop-monitor') });
    this._record('developer:build-exe', result.output);
    return result;
  }
}

module.exports = { DEVELOPER_ACTIONS, DesktopControl, HARNESS_ACTIONS, assertAction, commandNeedsShell, processExists, readPid };
