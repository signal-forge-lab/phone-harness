import assert from "node:assert/strict";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
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
