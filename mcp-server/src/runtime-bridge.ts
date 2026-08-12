import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { createInterface } from "node:readline";
import { dirname, resolve } from "node:path";
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

  constructor(
    private readonly python = process.env.PHONE_HARNESS_PYTHON || "python",
    private readonly timeoutMs = Number(process.env.PHONE_HARNESS_MCP_RUNTIME_TIMEOUT_MS || 120_000),
  ) {}

  call(method: string, params: Record<string, unknown> = {}): Promise<Record<string, unknown>> {
    this.ensureChild();
    return this.peer!.call(method, params);
  }

  async close(): Promise<void> {
    this.peer?.close();
    const child = this.child;
    this.peer = undefined;
    this.child = undefined;
    if (!child || child.exitCode !== null) return;
    child.kill();
    await new Promise<void>((resolveClose) => {
      const timer = setTimeout(() => {
        child.kill("SIGKILL");
        resolveClose();
      }, 2_000);
      child.once("exit", () => {
        clearTimeout(timer);
        resolveClose();
      });
    });
  }

  private ensureChild(): void {
    if (this.child && this.peer && this.child.exitCode === null) return;
    const sourceDirectory = dirname(fileURLToPath(import.meta.url));
    const mcpRoot = resolve(sourceDirectory, "..");
    const repoRoot = resolve(mcpRoot, "..");
    const bridgeScript = resolve(mcpRoot, "python_bridge.py");
    const pythonPath = resolve(repoRoot, "src");
    const child = spawn(this.python, ["-u", bridgeScript], {
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
}
