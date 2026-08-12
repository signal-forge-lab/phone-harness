import assert from "node:assert/strict";
import test from "node:test";
import { createPhoneMcpHandler, MODERN_MCP_PROTOCOL_VERSION } from "./server.js";
import type { RuntimeBridge } from "./runtime-bridge.js";

class FakeRuntime implements RuntimeBridge {
  readonly calls: Array<{ method: string; params: Record<string, unknown> }> = [];

  async call(method: string, params: Record<string, unknown> = {}): Promise<Record<string, unknown>> {
    this.calls.push({ method, params });
    if (method === "status") return { contract_version: 2, connection_state: "ready" };
    if (method === "observe") {
      return {
        contract_version: 2,
        observation_id: 7,
        source: "accessibility",
        elements: [{ text: "Search", x: 10, y: 20 }],
        ...(params.include_image ? { _image: { mime_type: "image/png", data: "aGVsbG8=" } } : {}),
      };
    }
    if (method === "act") return { contract_version: 2, count: 1, backend: "wda_batch" };
    throw new Error(`unexpected method ${method}`);
  }

  async close(): Promise<void> {}
}

test("modern server answers the 2026-07-28 discovery request without a session", async (t) => {
  const runtime = new FakeRuntime();
  const handler = createPhoneMcpHandler(runtime);
  t.after(() => handler.close());

  const response = await postModern(handler, "server/discover", {});
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("mcp-session-id"), null);
  const body = await response.json() as { result?: { supportedVersions?: string[] } };
  assert.ok(body.result?.supportedVersions?.includes(MODERN_MCP_PROTOCOL_VERSION));
});

test("modern server exposes exactly the three phone tools with safe annotations", async (t) => {
  const handler = createPhoneMcpHandler(new FakeRuntime());
  t.after(() => handler.close());

  const response = await postModern(handler, "tools/list", {});
  assert.equal(response.status, 200);
  const body = await response.json() as {
    result?: { tools?: Array<{ name: string; annotations?: Record<string, unknown> }> };
  };
  const tools = body.result?.tools ?? [];
  assert.deepEqual(tools.map((tool) => tool.name).sort(), ["phone_act", "phone_observe", "phone_status"]);
  assert.equal(tools.find((tool) => tool.name === "phone_status")?.annotations?.readOnlyHint, true);
  assert.equal(tools.find((tool) => tool.name === "phone_observe")?.annotations?.readOnlyHint, true);
  assert.equal(tools.find((tool) => tool.name === "phone_act")?.annotations?.readOnlyHint, false);
});

test("phone_observe returns semantic data and only includes an image when requested", async (t) => {
  const runtime = new FakeRuntime();
  const handler = createPhoneMcpHandler(runtime);
  t.after(() => handler.close());

  const response = await postModern(handler, "tools/call", {
    name: "phone_observe",
    arguments: { include_image: true },
  });
  assert.equal(response.status, 200, await response.clone().text());
  const body = await response.json() as {
    result?: { content?: Array<{ type?: string; data?: string }>; structuredContent?: Record<string, unknown> };
  };
  assert.equal(body.result?.structuredContent?.observation_id, 7);
  assert.equal(Object.hasOwn(body.result?.structuredContent ?? {}, "_image"), false);
  assert.ok(body.result?.content?.some((item) => item.type === "image" && item.data === "aGVsbG8="));
});

test("phone_act forwards observation id and batch without inventing phone logic", async (t) => {
  const runtime = new FakeRuntime();
  const handler = createPhoneMcpHandler(runtime);
  t.after(() => handler.close());

  const response = await postModern(handler, "tools/call", {
    name: "phone_act",
    arguments: {
      observation_id: 7,
      actions: [{ op: "tap_text", text: "Search", exact: true }],
    },
  });
  assert.equal(response.status, 200, await response.clone().text());
  assert.deepEqual(runtime.calls.at(-1), {
    method: "act",
    params: { observation_id: 7, actions: [{ op: "tap_text", text: "Search", exact: true }] },
  });
});

test("legacy initialize is rejected by the dedicated modern server", async (t) => {
  const handler = createPhoneMcpHandler(new FakeRuntime());
  t.after(() => handler.close());
  const response = await handler.fetch(new Request("https://example.test/mcp", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      jsonrpc: "2.0",
      id: 1,
      method: "initialize",
      params: { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "legacy", version: "1" } },
    }),
  }));
  assert.notEqual(response.status, 200);
});

function postModern(
  handler: { fetch(request: Request): Promise<Response> },
  method: string,
  params: Record<string, unknown>,
): Promise<Response> {
  return handler.fetch(new Request("https://example.test/mcp", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "mcp-method": method,
      "mcp-protocol-version": MODERN_MCP_PROTOCOL_VERSION,
      ...(typeof params.name === "string" ? { "mcp-name": params.name } : {}),
    },
    body: JSON.stringify({
      jsonrpc: "2.0",
      id: `modern-${method}`,
      method,
      params: {
        ...params,
        _meta: {
          "io.modelcontextprotocol/protocolVersion": MODERN_MCP_PROTOCOL_VERSION,
          "io.modelcontextprotocol/clientCapabilities": {},
        },
      },
    }),
  }));
}
