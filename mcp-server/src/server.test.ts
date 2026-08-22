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
      const mimeType = params.image_profile && params.image_profile !== "full" ? "image/jpeg" : "image/png";
      return {
        contract_version: 2,
        observation_id: 7,
        source: "accessibility",
        elements: [{ text: "Search", x: 10, y: 20 }],
        ...(params.include_image || params.mode === "visual" ? { _image: { mime_type: mimeType, data: "aGVsbG8=" } } : {}),
      };
    }
    if (method === "act") return { contract_version: 2, count: 1, backend: "wda_batch" };
    if (method === "decision") return { recorded: true };
    if (method === "host_activity") return { recorded: true, state: params.state };
    if (method === "operator_question") return { answered: true, answer: "B", choice: "B" };
    if (method === "teaching_next") {
      return {
        pending: true,
        message_id: "teach-1",
        text: "This item is level 3.",
        _image: { mime_type: "image/webp", data: "aGVsbG8=" },
      };
    }
    if (method === "teaching_handle") return { handled: true, message_id: params.message_id };
    if (method === "workflow") return { executed: true, workflow: params.name };
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

test("modern server exposes phone tools with safe annotations", async (t) => {
  const handler = createPhoneMcpHandler(new FakeRuntime());
  t.after(() => handler.close());

  const response = await postModern(handler, "tools/list", {});
  assert.equal(response.status, 200);
  const body = await response.json() as {
    result?: { tools?: Array<{ name: string; annotations?: Record<string, unknown>; inputSchema?: { properties?: Record<string, unknown> } }> };
  };
  const tools = body.result?.tools ?? [];
  assert.deepEqual(tools.map((tool) => tool.name).sort(), ["phone_act", "phone_host_activity", "phone_observe", "phone_status", "phone_teaching"]);
  assert.equal(tools.find((tool) => tool.name === "phone_status")?.annotations?.readOnlyHint, true);
  const observe = tools.find((tool) => tool.name === "phone_observe");
  assert.equal(observe?.annotations?.readOnlyHint, true);
  assert.equal(Object.hasOwn(observe?.inputSchema?.properties ?? {}, "region"), true);
  assert.equal(Object.hasOwn(observe?.inputSchema?.properties ?? {}, "mode"), true);
  assert.equal(Object.hasOwn(observe?.inputSchema?.properties ?? {}, "image_profile"), true);
  assert.equal(Object.hasOwn(observe?.inputSchema?.properties ?? {}, "reuse_observation_id"), true);
  assert.equal(Object.hasOwn(observe?.inputSchema?.properties ?? {}, "visual_grid"), false);
  assert.equal(tools.find((tool) => tool.name === "phone_act")?.annotations?.readOnlyHint, false);
  assert.equal(Object.hasOwn(tools.find((tool) => tool.name === "phone_act")?.inputSchema?.properties ?? {}, "decision_context"), true);
  assert.equal(tools.find((tool) => tool.name === "phone_teaching")?.annotations?.destructiveHint, false);
  assert.equal(tools.find((tool) => tool.name === "phone_host_activity")?.annotations?.destructiveHint, false);
});

test("phone_host_activity forwards explicit monitor lifecycle state", async (t) => {
  const runtime = new FakeRuntime();
  const handler = createPhoneMcpHandler(runtime);
  t.after(() => handler.close());
  const response = await postModern(handler, "tools/call", {
    name: "phone_host_activity",
    arguments: { state: "completed", note: "P1 complete" },
  });
  assert.equal(response.status, 200, await response.clone().text());
  assert.deepEqual(runtime.calls.at(-1), {
    method: "host_activity",
    params: { state: "completed", note: "P1 complete" },
  });
});

test("phone_teaching returns the oldest pending Human Teaching message with image content", async (t) => {
  const runtime = new FakeRuntime();
  const handler = createPhoneMcpHandler(runtime);
  t.after(() => handler.close());
  const response = await postModern(handler, "tools/call", {
    name: "phone_teaching",
    arguments: { action: "next" },
  });
  assert.equal(response.status, 200, await response.clone().text());
  const body = await response.json() as {
    result?: { content?: Array<{ type?: string; mimeType?: string }>; structuredContent?: Record<string, unknown> };
  };
  assert.equal(body.result?.structuredContent?.message_id, "teach-1");
  assert.ok(body.result?.content?.some((item) => item.type === "image" && item.mimeType === "image/webp"));
  assert.deepEqual(runtime.calls.at(-1), { method: "teaching_next", params: {} });
});

test("phone_teaching can acknowledge learning and send a reply", async (t) => {
  const runtime = new FakeRuntime();
  const handler = createPhoneMcpHandler(runtime);
  t.after(() => handler.close());
  const response = await postModern(handler, "tools/call", {
    name: "phone_teaching",
    arguments: { action: "handle", message_id: "teach-1", reply: "Learned.", learned: true },
  });
  assert.equal(response.status, 200, await response.clone().text());
  assert.deepEqual(runtime.calls.at(-1), {
    method: "teaching_handle",
    params: { message_id: "teach-1", reply: "Learned.", learned: true },
  });
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
  assert.deepEqual(runtime.calls.at(-1), {
    method: "observe",
    params: { force: false, include_image: true, include_text_content: false },
  });
});

test("phone_observe forwards explicit full-text request", async (t) => {
  const runtime = new FakeRuntime();
  const handler = createPhoneMcpHandler(runtime);
  t.after(() => handler.close());
  const response = await postModern(handler, "tools/call", {
    name: "phone_observe",
    arguments: { include_text_content: true },
  });
  assert.equal(response.status, 200, await response.clone().text());
  assert.deepEqual(runtime.calls.at(-1), {
    method: "observe",
    params: { force: false, include_image: false, include_text_content: true },
  });
});

test("phone_observe forwards an optional absolute-screen region", async (t) => {
  const runtime = new FakeRuntime();
  const handler = createPhoneMcpHandler(runtime);
  t.after(() => handler.close());
  const region = { x: 10, y: 20, w: 300, h: 400 };
  const response = await postModern(handler, "tools/call", {
    name: "phone_observe",
    arguments: { region },
  });
  assert.equal(response.status, 200, await response.clone().text());
  assert.deepEqual(runtime.calls.at(-1), {
    method: "observe",
    params: { force: false, include_image: false, include_text_content: false, region },
  });
});

test("phone_observe forwards visual-only mode and coarse image profile", async (t) => {
  const runtime = new FakeRuntime();
  const handler = createPhoneMcpHandler(runtime);
  t.after(() => handler.close());
  const response = await postModern(handler, "tools/call", {
    name: "phone_observe",
    arguments: { mode: "visual", image_profile: "glance" },
  });
  assert.equal(response.status, 200, await response.clone().text());
  const body = await response.json() as {
    result?: { content?: Array<{ type?: string; mimeType?: string }> };
  };
  assert.ok(body.result?.content?.some((item) => item.type === "image" && item.mimeType === "image/jpeg"));
  assert.deepEqual(runtime.calls.at(-1), {
    method: "observe",
    params: {
      force: false,
      include_image: false,
      include_text_content: false,
      mode: "visual",
      image_profile: "glance",
    },
  });
});

test("phone_observe forwards retained-frame visual refinement", async (t) => {
  const runtime = new FakeRuntime();
  const handler = createPhoneMcpHandler(runtime);
  t.after(() => handler.close());
  const response = await postModern(handler, "tools/call", {
    name: "phone_observe",
    arguments: { mode: "visual", image_profile: "detail", reuse_observation_id: 7 },
  });
  assert.equal(response.status, 200, await response.clone().text());
  assert.deepEqual(runtime.calls.at(-1), {
    method: "observe",
    params: {
      force: false,
      include_image: false,
      include_text_content: false,
      mode: "visual",
      image_profile: "detail",
      reuse_observation_id: 7,
    },
  });
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

test("phone_act accepts observation element refs", async (t) => {
  const runtime = new FakeRuntime();
  const handler = createPhoneMcpHandler(runtime);
  t.after(() => handler.close());
  const response = await postModern(handler, "tools/call", {
    name: "phone_act",
    arguments: { observation_id: 7, actions: [{ op: "tap_element", element_ref: "e3" }] },
  });
  assert.equal(response.status, 200, await response.clone().text());
  assert.deepEqual(runtime.calls.at(-1), {
    method: "act",
    params: { observation_id: 7, actions: [{ op: "tap_element", element_ref: "e3" }] },
  });
});

test("phone_act records explicit host decision context before the device action", async (t) => {
  const runtime = new FakeRuntime();
  const handler = createPhoneMcpHandler(runtime);
  t.after(() => handler.close());
  const response = await postModern(handler, "tools/call", {
    name: "phone_act",
    arguments: {
      observation_id: 7,
      decision_context: {
        summary: "Merge the two strict visual matches",
        confidence: 0.97,
        expected_effect: "Free one board cell",
      },
      actions: [{ op: "tap", x: 10, y: 20 }],
    },
  });
  assert.equal(response.status, 200, await response.clone().text());
  assert.deepEqual(runtime.calls.at(-2), {
    method: "decision",
    params: {
      summary: "Merge the two strict visual matches",
      confidence: 0.97,
      expected_effect: "Free one board cell",
    },
  });
  assert.equal(runtime.calls.at(-1)?.method, "act");
});

test("phone_act can ask the Windows operator without changing the phone", async (t) => {
  const runtime = new FakeRuntime();
  const handler = createPhoneMcpHandler(runtime);
  t.after(() => handler.close());
  const response = await postModern(handler, "tools/call", {
    name: "phone_act",
    arguments: {
      actions: [{
        op: "ask_operator",
        question: "Which icon is correct?",
        choices: ["A", "B"],
        confidence: 0.55,
        impact: "high",
        timeout: 30,
      }],
    },
  });
  assert.equal(response.status, 200, await response.clone().text());
  assert.deepEqual(runtime.calls.at(-1), {
    method: "operator_question",
    params: {
      question: "Which icon is correct?",
      choices: ["A", "B"],
      confidence: 0.55,
      impact: "high",
      timeout: 30,
    },
  });
});

test("phone_act forwards one bounded Merge Boss workflow through the persistent runtime", async (t) => {
  const runtime = new FakeRuntime();
  const handler = createPhoneMcpHandler(runtime);
  t.after(() => handler.close());
  const response = await postModern(handler, "tools/call", {
    name: "phone_act",
    arguments: {
      actions: [{
        op: "run_workflow",
        name: "merge_boss_once",
        max_producer_taps: 4,
        max_relaxed_checks: 2,
      }],
    },
  });
  assert.equal(response.status, 200, await response.clone().text());
  assert.deepEqual(runtime.calls.at(-1), {
    method: "workflow",
    params: {
      name: "merge_boss_once",
      options: { max_producer_taps: 4, max_relaxed_checks: 2 },
    },
  });
});

test("phone_act forwards the multi-action order-aware Merge Boss turn budgets", async (t) => {
  const runtime = new FakeRuntime();
  const handler = createPhoneMcpHandler(runtime);
  t.after(() => handler.close());
  const response = await postModern(handler, "tools/call", {
    name: "phone_act",
    arguments: {
      actions: [{
        op: "run_workflow",
        name: "merge_boss_turn",
        max_cycles: 10,
        max_merges: 24,
        max_emissions: 20,
        uncertain_burst_size: 6,
        max_recoveries: 3,
        order_rescan_every: 6,
      }],
    },
  });
  assert.equal(response.status, 200, await response.clone().text());
  assert.deepEqual(runtime.calls.at(-1), {
    method: "workflow",
    params: {
      name: "merge_boss_turn",
      options: {
        max_cycles: 10,
        max_merges: 24,
        max_emissions: 20,
        uncertain_burst_size: 6,
        max_recoveries: 3,
        order_rescan_every: 6,
      },
    },
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
