import { createPhoneMcpHandler, MODERN_MCP_PROTOCOL_VERSION } from "../src/server.js";
import { PythonRuntimeBridge } from "../src/runtime-bridge.js";

interface ToolResponse {
  result?: {
    structuredContent?: Record<string, unknown>;
    isError?: boolean;
  };
}

interface ElementRecord {
  text?: string;
  name?: string;
  role?: string;
}

const runtime = new PythonRuntimeBridge();
const handler = createPhoneMcpHandler(runtime);

try {
  const discovery = await modern("server/discover", {});
  const supported = asObject(discovery.result)?.supportedVersions;
  if (!Array.isArray(supported) || !supported.includes(MODERN_MCP_PROTOCOL_VERSION)) {
    throw new Error("Modern 2026-07-28 discovery failed");
  }

  const statusResponse = await modern("tools/call", { name: "phone_status", arguments: {} });
  const status = structured(statusResponse);
  if (status.connection_state !== "ready") {
    throw new Error(`phone_status is not ready: ${String(status.connection_state)}`);
  }

  const observedResponse = await modern("tools/call", {
    name: "phone_observe",
    arguments: { force: true, include_image: false },
  });
  const observed = structured(observedResponse);
  const elements = Array.isArray(observed.elements) ? observed.elements.filter(isElement) : [];
  const byName = new Map(elements.filter((item) => item.name).map((item) => [item.name!, item]));
  const clear = byName.get("Clear") ?? byName.get("AllClear");
  if (!clear?.text || !byName.has("One") || !byName.has("Equals")) {
    throw new Error("Safety stop: Calculator keypad is not the active observed screen");
  }
  if (!Number.isInteger(observed.observation_id)) {
    throw new Error("phone_observe did not return an observation_id");
  }

  const actionResponse = await modern("tools/call", {
    name: "phone_act",
    arguments: {
      observation_id: observed.observation_id,
      actions: [{ op: "tap_text", text: clear.text, exact: true }],
    },
  });
  const action = structured(actionResponse);

  const finalResponse = await modern("tools/call", {
    name: "phone_observe",
    arguments: { force: true, include_image: false },
  });
  const finalObservation = structured(finalResponse);
  const finalElements = Array.isArray(finalObservation.elements)
    ? finalObservation.elements.filter(isElement)
    : [];
  const displayValues = finalElements
    .filter((item) => item.role === "XCUIElementTypeStaticText" || item.role === "XCUIElementTypeTextField")
    .map((item) => item.text)
    .filter((value): value is string => typeof value === "string");

  console.log(JSON.stringify({
    protocol: MODERN_MCP_PROTOCOL_VERSION,
    connection_state: status.connection_state,
    active_transport: status.active_transport ?? null,
    observation_source: observed.source ?? null,
    observation_element_count: elements.length,
    act_backend: action.backend ?? null,
    act_count: action.count ?? null,
    act_duration_ms: action.duration_ms ?? null,
    act_subprocess_count: action.subprocess_count ?? null,
    calculator_display: displayValues,
  }));
} finally {
  await handler.close();
  await runtime.close();
}

async function modern(method: string, params: Record<string, unknown>): Promise<Record<string, unknown>> {
  const response = await handler.fetch(new Request("https://phone-harness.local/mcp", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "mcp-method": method,
      "mcp-protocol-version": MODERN_MCP_PROTOCOL_VERSION,
      ...(typeof params.name === "string" ? { "mcp-name": params.name } : {}),
    },
    body: JSON.stringify({
      jsonrpc: "2.0",
      id: `smoke-${method}-${typeof params.name === "string" ? params.name : "request"}`,
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
  const body = await response.json() as Record<string, unknown>;
  if (!response.ok) throw new Error(`Modern MCP request failed (${response.status}): ${JSON.stringify(body)}`);
  return body;
}

function structured(response: Record<string, unknown>): Record<string, unknown> {
  const result = asObject(response.result) as ToolResponse["result"] | undefined;
  if (!result || result.isError) throw new Error(`MCP tool returned an error: ${JSON.stringify(response)}`);
  const content = result.structuredContent;
  if (!content || typeof content !== "object") throw new Error("MCP tool did not return structuredContent");
  return content;
}

function asObject(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : undefined;
}

function isElement(value: unknown): value is ElementRecord {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}
