import assert from "node:assert/strict";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { mkdtempSync, rmSync, statSync, utimesSync, writeFileSync } from "node:fs";
import test from "node:test";
import { PassThrough } from "node:stream";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { JsonLinePeer, PythonRuntimeBridge, RuntimeBridgeError, runtimeSourceRevision } from "./runtime-bridge.js";

test("json line peer matches responses to concurrent request ids", async () => {
  const input = new PassThrough();
  const output = new PassThrough();
  const peer = new JsonLinePeer(input, output);
  const first = peer.call("status", {});
  const second = peer.call("observe", { force: true });
  const written = output.read()?.toString() ?? "";
  const requests = written.trim().split("\n").map((line: string) => JSON.parse(line) as { id: number });
  input.write(`${JSON.stringify({ id: requests[1]!.id, ok: true, result: { observation_id: 9 } })}\n`);
  input.write(`${JSON.stringify({ id: requests[0]!.id, ok: true, result: { connection_state: "ready" } })}\n`);
  assert.deepEqual(await first, { connection_state: "ready" });
  assert.deepEqual(await second, { observation_id: 9 });
  peer.close();
});

test("json line peer rejects only the matching request with machine-readable runtime error", async () => {
  const input = new PassThrough();
  const output = new PassThrough();
  const peer = new JsonLinePeer(input, output);
  const pending = peer.call("act", { actions: [] });
  const request = JSON.parse((output.read()?.toString() ?? "").trim()) as { id: number };
  input.write(`${JSON.stringify({
    id: request.id,
    ok: false,
    error: { code: "INVALID_REQUEST", message: "Invalid phone request", retryable: false, phase: "preflight" },
  })}\n`);
  await assert.rejects(pending, (error: unknown) => {
    return error instanceof RuntimeBridgeError && error.code === "INVALID_REQUEST";
  });
  peer.close();
});

test("missing Python executable rejects the request instead of crashing the MCP process", async () => {
  const bridge = new PythonRuntimeBridge("definitely-missing-phone-harness-python", 2_000);
  await assert.rejects(bridge.call("status", {}));
  await bridge.close();
});

test("runtime source revision tracks Python files and ignores unrelated file content", () => {
  const directory = mkdtempSync(join(tmpdir(), "phone-harness-runtime-watch-"));
  try {
    const source = join(directory, "runtime.py");
    const note = join(directory, "note.txt");
    writeFileSync(source, "VALUE = 1\n");
    writeFileSync(note, "first\n");
    const initial = runtimeSourceRevision([directory]);
    writeFileSync(note, "second and longer\n");
    assert.equal(runtimeSourceRevision([directory]), initial);
    writeFileSync(source, "VALUE = 200\n");
    assert.notEqual(runtimeSourceRevision([directory]), initial);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test("runtime source revision ignores metadata-only file replacement when size and mtime are preserved", () => {
  const directory = mkdtempSync(join(tmpdir(), "phone-harness-runtime-metadata-"));
  try {
    const source = join(directory, "runtime.py");
    writeFileSync(source, "VALUE = 1\n");
    // Normalize to an exact millisecond first so the comparison exercises only
    // ctime/file-identity churn rather than platform-specific sub-ms rounding
    // in utimesSync().
    const normalized = Math.floor(statSync(source).mtimeMs) / 1000;
    utimesSync(source, normalized, normalized);
    const before = statSync(source);
    const initial = runtimeSourceRevision([directory]);
    rmSync(source);
    writeFileSync(source, "VALUE = 1\n");
    utimesSync(source, before.atime, before.mtime);
    assert.equal(runtimeSourceRevision([directory]), initial);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test("Python runtime bridge rotates changed source only after in-flight calls finish", async () => {
  const directory = mkdtempSync(join(tmpdir(), "phone-harness-runtime-rotate-"));
  const watched = join(directory, "runtime.py");
  writeFileSync(watched, "VALUE = 1\n");
  const responder = [
    "const readline = require('node:readline');",
    "const rl = readline.createInterface({ input: process.stdin });",
    "rl.on('line', (line) => {",
    "  const request = JSON.parse(line);",
    "  const delay = request.method === 'slow' ? 120 : 0;",
    "  setTimeout(() => process.stdout.write(JSON.stringify({ id: request.id, ok: true, result: { pid: process.pid } }) + '\\n'), delay);",
    "});",
  ].join("\n");
  const processFactory = (): ChildProcessWithoutNullStreams => spawn(
    process.execPath,
    ["-e", responder],
    { windowsHide: true, stdio: ["pipe", "pipe", "pipe"] },
  );
  const bridge = new PythonRuntimeBridge("unused", 2_000, [watched], processFactory);
  try {
    const first = await bridge.call("status", {});
    const firstPid = first.pid;
    const slow = bridge.call("slow", {});
    await new Promise((resolveDelay) => setTimeout(resolveDelay, 20));
    writeFileSync(watched, "VALUE = 200\n");
    const concurrent = await bridge.call("status", {});
    assert.equal(concurrent.pid, firstPid);
    assert.equal((await slow).pid, firstPid);
    const afterRotation = await bridge.call("status", {});
    assert.notEqual(afterRotation.pid, firstPid);
  } finally {
    await bridge.close();
    rmSync(directory, { recursive: true, force: true });
  }
});

test("Python runtime bridge defers source rotation until the next request after a successful response", async () => {
  const directory = mkdtempSync(join(tmpdir(), "phone-harness-runtime-deferred-rotate-"));
  const watched = join(directory, "runtime.py");
  writeFileSync(watched, "VALUE = 1\n");
  const responder = [
    "const readline = require('node:readline');",
    "const rl = readline.createInterface({ input: process.stdin });",
    "rl.on('line', (line) => {",
    "  const request = JSON.parse(line);",
    "  process.stdout.write(JSON.stringify({ id: request.id, ok: true, result: { pid: process.pid } }) + '\\n');",
    "});",
  ].join("\n");
  const children: ChildProcessWithoutNullStreams[] = [];
  const processFactory = (): ChildProcessWithoutNullStreams => {
    const child = spawn(
      process.execPath,
      ["-e", responder],
      { windowsHide: true, stdio: ["pipe", "pipe", "pipe"] },
    );
    children.push(child);
    return child;
  };
  const bridge = new PythonRuntimeBridge("unused", 2_000, [watched], processFactory);
  try {
    const first = await bridge.call("status", {});
    const firstPid = first.pid;
    writeFileSync(watched, "VALUE = 200\n");

    const changedSourceCall = await bridge.call("workflow", {});
    assert.equal(changedSourceCall.pid, firstPid);
    assert.equal(children.length, 1);
    assert.equal(children[0]!.exitCode, null, "successful response must not be followed by same-call teardown");

    const afterRotation = await bridge.call("status", {});
    assert.notEqual(afterRotation.pid, firstPid);
    assert.equal(children.length, 2);
  } finally {
    await bridge.close();
    for (const child of children) {
      if (child.exitCode === null) child.kill();
    }
    rmSync(directory, { recursive: true, force: true });
  }
});

test("Python runtime bridge keeps production runtime stable when hot reload is not explicitly enabled", async () => {
  const directory = mkdtempSync(join(tmpdir(), "phone-harness-runtime-no-hot-reload-"));
  const watched = join(directory, "runtime.py");
  writeFileSync(watched, "VALUE = 1\n");
  const previousHotReload = process.env.PHONE_HARNESS_MCP_RUNTIME_HOT_RELOAD;
  const previousExtraWatch = process.env.PHONE_HARNESS_MCP_RUNTIME_WATCH_PATHS;
  delete process.env.PHONE_HARNESS_MCP_RUNTIME_HOT_RELOAD;
  process.env.PHONE_HARNESS_MCP_RUNTIME_WATCH_PATHS = watched;
  const responder = [
    "const readline = require('node:readline');",
    "const rl = readline.createInterface({ input: process.stdin });",
    "rl.on('line', (line) => {",
    "  const request = JSON.parse(line);",
    "  process.stdout.write(JSON.stringify({ id: request.id, ok: true, result: { pid: process.pid } }) + '\\n');",
    "});",
  ].join("\n");
  const processFactory = (): ChildProcessWithoutNullStreams => spawn(
    process.execPath,
    ["-e", responder],
    { windowsHide: true, stdio: ["pipe", "pipe", "pipe"] },
  );
  const bridge = new PythonRuntimeBridge("unused", 2_000, undefined, processFactory);
  try {
    const first = await bridge.call("status", {});
    writeFileSync(watched, "VALUE = 200\n");
    await new Promise((resolveDelay) => setTimeout(resolveDelay, 20));
    const second = await bridge.call("status", {});
    const third = await bridge.call("status", {});
    assert.equal(second.pid, first.pid);
    assert.equal(third.pid, first.pid);
  } finally {
    await bridge.close();
    if (previousHotReload === undefined) delete process.env.PHONE_HARNESS_MCP_RUNTIME_HOT_RELOAD;
    else process.env.PHONE_HARNESS_MCP_RUNTIME_HOT_RELOAD = previousHotReload;
    if (previousExtraWatch === undefined) delete process.env.PHONE_HARNESS_MCP_RUNTIME_WATCH_PATHS;
    else process.env.PHONE_HARNESS_MCP_RUNTIME_WATCH_PATHS = previousExtraWatch;
    rmSync(directory, { recursive: true, force: true });
  }
});
