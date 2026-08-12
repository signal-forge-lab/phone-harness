import assert from "node:assert/strict";
import test from "node:test";
import { PassThrough } from "node:stream";
import { JsonLinePeer, PythonRuntimeBridge, RuntimeBridgeError } from "./runtime-bridge.js";

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
