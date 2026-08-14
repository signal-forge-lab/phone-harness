import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { readdirSync, statSync } from "node:fs";
import { createInterface } from "node:readline";
import { delimiter, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import type { Readable, Writable } from "node:stream";

export interface RuntimeBridge {
  call(method: string, params?: Record<string, unknown>): Promise<Record<string, unknown>>;
  close(): Promise<void>;
}

export class RuntimeBridgeError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly detail: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "RuntimeBridgeError";
  }
}

interface PendingRequest {
  resolve(value: Record<string, unknown>): void;
  reject(error: Error): void;
  timer: NodeJS.Timeout;
}

interface BridgeResponse {
  id?: number;
  ok?: boolean;
  result?: Record<string, unknown>;
  error?: Record<string, unknown>;
}

type RuntimeProcessFactory = () => ChildProcessWithoutNullStreams;

function collectPythonSourceRevision(path: string, entries: string[]): void {
  let stat;
  try {
    stat = statSync(path);
  } catch {
    entries.push(`${path}:missing`);
    return;
  }
  if (stat.isDirectory()) {
    for (const child of readdirSync(path).sort()) {
      collectPythonSourceRevision(resolve(path, child), entries);
    }
    return;
  }
  if (!path.endsWith(".py")) return;
  entries.push(`${path}:${stat.size}:${stat.mtimeMs}:${stat.ctimeMs}`);
}

export function runtimeSourceRevision(paths: readonly string[]): string {
  const entries: string[] = [];
  for (const path of paths) collectPythonSourceRevision(path, entries);
  return entries.join("|");
}

function defaultRuntimeWatchPaths(): string[] {
  const sourceDirectory = dirname(fileURLToPath(import.meta.url));
  const mcpRoot = resolve(sourceDirectory, "..");
  const repoRoot = resolve(mcpRoot, "..");
  const configured = (process.env.PHONE_HARNESS_MCP_RUNTIME_WATCH_PATHS ?? "")
    .split(delimiter)
    .map((path) => path.trim())
    .filter(Boolean)
    .map((path) => resolve(repoRoot, path));
  return [
    resolve(mcpRoot, "python_bridge.py"),
    resolve(repoRoot, "src", "phone_harness"),
    ...configured,
  ];
}

export class JsonLinePeer {
  private readonly pending = new Map<number, PendingRequest>();
  private nextId = 1;
  private closed = false;

  constructor(
    input: Readable,
    private readonly output: Writable,
    private readonly timeoutMs = 120_000,
  ) {
    const lines = createInterface({ input, crlfDelay: Infinity });
    lines.on("line", (line) => this.handleLine(line));
    lines.on("close", () => this.failAll(new Error("phone runtime bridge closed")));
    input.on("error", (error) => this.failAll(error));
    output.on("error", (error) => this.failAll(error));
  }

  call(method: string, params: Record<string, unknown> = {}): Promise<Record<string, unknown>> {
    if (this.closed) return Promise.reject(new Error("phone runtime bridge is closed"));
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`phone runtime bridge timed out: ${method}`));
      }, this.timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      this.output.write(`${JSON.stringify({ id, method, params })}\n`);
    });
  }

  close(): void {
    if (this.closed) return;
    this.closed = true;
    this.failAll(new Error("phone runtime bridge closed"));
  }

  private handleLine(line: string): void {
    let response: BridgeResponse;
    try {
      response = JSON.parse(line) as BridgeResponse;
    } catch {
      this.failAll(new Error("phone runtime bridge returned invalid JSON"));
      return;
    }
    if (!Number.isInteger(response.id)) return;
    const pending = this.pending.get(response.id!);
    if (!pending) return;
    this.pending.delete(response.id!);
    clearTimeout(pending.timer);
    if (response.ok === true && response.result && typeof response.result === "object") {
      pending.resolve(response.result);
      return;
    }
    const error = response.error ?? {};
    pending.reject(new RuntimeBridgeError(
      typeof error.code === "string" ? error.code : "RUNTIME_ERROR",
      typeof error.message === "string" ? error.message : "Phone runtime request failed",
      error,
    ));
  }

  private failAll(error: Error): void {
    if (this.closed && this.pending.size === 0) return;
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.pending.clear();
  }
}

export class PythonRuntimeBridge implements RuntimeBridge {
  private child?: ChildProcessWithoutNullStreams;
  private peer?: JsonLinePeer;
  private sourceRevision?: string;
  private activeCalls = 0;
  private rotationPending = false;
  private lifecycle: Promise<void> = Promise.resolve();

  constructor(
    private readonly python = process.env.PHONE_HARNESS_PYTHON || "python",
    private readonly timeoutMs = Number(process.env.PHONE_HARNESS_MCP_RUNTIME_TIMEOUT_MS || 120_000),
    private readonly watchPaths: readonly string[] = defaultRuntimeWatchPaths(),
    private readonly processFactory?: RuntimeProcessFactory,
  ) {}

  async call(method: string, params: Record<string, unknown> = {}): Promise<Record<string, unknown>> {
    let peer!: JsonLinePeer;
    await this.runLifecycle(async () => {
      this.refreshSourceRevision();
      if (this.rotationPending && this.activeCalls === 0) await this.stopChild();
      this.ensureChild();
      this.activeCalls += 1;
      peer = this.peer!;
    });
    try {
      return await peer.call(method, params);
    } finally {
      await this.runLifecycle(async () => {
        this.activeCalls = Math.max(0, this.activeCalls - 1);
        this.refreshSourceRevision();
        if (this.rotationPending && this.activeCalls === 0) await this.stopChild();
      });
    }
  }

  async close(): Promise<void> {
    await this.runLifecycle(() => this.stopChild());
  }

  private runLifecycle(operation: () => Promise<void> | void): Promise<void> {
    const next = this.lifecycle.then(operation, operation);
    this.lifecycle = next.then(() => undefined, () => undefined);
    return next;
  }

  private refreshSourceRevision(): void {
    const revision = runtimeSourceRevision(this.watchPaths);
    if (this.sourceRevision === undefined) {
      this.sourceRevision = revision;
      return;
    }
    if (revision === this.sourceRevision) return;
    this.sourceRevision = revision;
    if (this.child) this.rotationPending = true;
  }

  private async stopChild(): Promise<void> {
    const peer = this.peer;
    const child = this.child;
    this.peer = undefined;
    this.child = undefined;
    this.rotationPending = false;
    if (!child || child.exitCode !== null) {
      peer?.close();
      return;
    }

    // EOF lets python_bridge.py run PhoneRuntime.close(), which tears down any
    // WDA runner owned by that Python runtime before a successor is spawned.
    child.stdin.end();
    if (!(await this.waitForExit(child, 5_000)) && child.exitCode === null) {
      child.kill();
      if (!(await this.waitForExit(child, 2_000)) && child.exitCode === null) {
        child.kill("SIGKILL");
      }
    }
    peer?.close();
  }

  private waitForExit(child: ChildProcessWithoutNullStreams, timeoutMs: number): Promise<boolean> {
    if (child.exitCode !== null) return Promise.resolve(true);
    return new Promise<boolean>((resolveExit) => {
      let done = false;
      const finish = (exited: boolean): void => {
        if (done) return;
        done = true;
        clearTimeout(timer);
        child.off("exit", onExit);
        resolveExit(exited);
      };
      const onExit = (): void => finish(true);
      const timer = setTimeout(() => finish(false), timeoutMs);
      child.once("exit", onExit);
    });
  }

  private ensureChild(): void {
    if (this.child && this.peer && this.child.exitCode === null) return;
    const child = this.processFactory ? this.processFactory() : this.spawnPythonRuntime();
    child.stderr.on("data", () => undefined);
    const peer = new JsonLinePeer(child.stdout, child.stdin, this.timeoutMs);
    child.once("error", () => {
      peer.close();
      if (this.child === child) {
        this.child = undefined;
        this.peer = undefined;
      }
    });
    child.once("exit", () => {
      peer.close();
      if (this.child === child) {
        this.child = undefined;
        this.peer = undefined;
      }
    });
    this.child = child;
    this.peer = peer;
  }

  private spawnPythonRuntime(): ChildProcessWithoutNullStreams {
    const sourceDirectory = dirname(fileURLToPath(import.meta.url));
    const mcpRoot = resolve(sourceDirectory, "..");
    const repoRoot = resolve(mcpRoot, "..");
    const bridgeScript = resolve(mcpRoot, "python_bridge.py");
    const pythonPath = resolve(repoRoot, "src");
    return spawn(this.python, ["-u", bridgeScript], {
      cwd: repoRoot,
      env: {
        ...process.env,
        PYTHONPATH: process.env.PYTHONPATH
          ? `${pythonPath}${process.platform === "win32" ? ";" : ":"}${process.env.PYTHONPATH}`
          : pythonPath,
      },
      windowsHide: true,
      stdio: ["pipe", "pipe", "pipe"],
    });
  }
}
