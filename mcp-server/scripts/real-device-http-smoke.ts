import { createHash, randomBytes } from "node:crypto";
import { mkdtempSync, rmSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { Server } from "node:http";
import { createHttpServer } from "../src/http-server.js";
import { PythonRuntimeBridge } from "../src/runtime-bridge.js";
import { MODERN_MCP_PROTOCOL_VERSION } from "../src/server.js";

interface ElementRecord {
  text?: string;
  name?: string;
  role?: string;
}

const port = await freePort();
const base = `http://127.0.0.1:${port}`;
const stateDirectory = mkdtempSync(join(tmpdir(), "phone-harness-mcp-device-"));
const ownerToken = randomBytes(32).toString("base64url");
process.env.PHONE_HARNESS_STATUS_FILE = join(stateDirectory, "runtime-status.json");

const runtime = new PythonRuntimeBridge();
const app = createHttpServer({
  host: "127.0.0.1",
  port,
  publicBaseUrl: `${base}/`,
  stateDirectory,
  ownerToken,
  allowedRedirectHosts: ["chatgpt.com"],
  allowedOrigins: [base, "https://chatgpt.com"],
}, runtime);

const listener = await new Promise<Server>((resolve) => {
  const server = app.app.listen(port, "127.0.0.1", () => resolve(server));
});

try {
  const redirectUri = "http://127.0.0.1:45679/callback";
  const registeredResponse = await fetch(`${base}/register`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      client_name: "phone-harness-real-device-smoke",
      redirect_uris: [redirectUri],
      token_endpoint_auth_method: "none",
      grant_types: ["authorization_code", "refresh_token"],
      response_types: ["code"],
    }),
  });
  if (registeredResponse.status !== 201) throw new Error(`DCR failed: ${await registeredResponse.text()}`);
  const registered = await registeredResponse.json() as { client_id: string };

  const verifier = randomBytes(48).toString("base64url");
  const challenge = createHash("sha256").update(verifier).digest("base64url");
  const resource = `${base}/mcp`;
  const fields = {
    response_type: "code",
    client_id: registered.client_id,
    redirect_uri: redirectUri,
    code_challenge: challenge,
    code_challenge_method: "S256",
    scope: "phone",
    state: "real-device-smoke",
    resource,
  };
  const approved = await fetch(`${base}/authorize`, {
    method: "POST",
    redirect: "manual",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ ...fields, owner_token: ownerToken }),
  });
  if (approved.status !== 302) throw new Error(`OAuth authorization failed: ${await approved.text()}`);
  const redirect = new URL(approved.headers.get("location") ?? "");
  const code = redirect.searchParams.get("code");
  if (!code) throw new Error("OAuth authorization did not return a code");

  const tokenResponse = await fetch(`${base}/token`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      code,
      code_verifier: verifier,
      client_id: registered.client_id,
      redirect_uri: redirectUri,
      resource,
    }),
  });
  if (!tokenResponse.ok) throw new Error(`OAuth token exchange failed: ${await tokenResponse.text()}`);
  const tokens = await tokenResponse.json() as { access_token: string };

  const status = await tool(tokens.access_token, "phone_status", {});
  if (status.connection_state !== "ready") throw new Error("phone_status is not ready");

  const observed = await tool(tokens.access_token, "phone_observe", { force: true, include_image: false });
  const elements = Array.isArray(observed.elements) ? observed.elements.filter(isElement) : [];
  const byName = new Map(elements.filter((item) => item.name).map((item) => [item.name!, item]));
  const clear = byName.get("Clear") ?? byName.get("AllClear");
  if (!clear?.text || !byName.has("One") || !byName.has("Equals")) {
    throw new Error("Safety stop: Calculator keypad is not the active observed screen");
  }
  if (!Number.isInteger(observed.observation_id)) throw new Error("phone_observe did not return observation_id");

  const action = await tool(tokens.access_token, "phone_act", {
    observation_id: observed.observation_id,
    actions: [{ op: "tap_text", text: clear.text, exact: true }],
  });
  const finalObservation = await tool(tokens.access_token, "phone_observe", { force: true, include_image: false });
  const finalElements = Array.isArray(finalObservation.elements)
    ? finalObservation.elements.filter(isElement)
    : [];
  const displayValues = finalElements
    .filter((item) => item.role === "XCUIElementTypeStaticText" || item.role === "XCUIElementTypeTextField")
    .map((item) => item.text)
    .filter((value): value is string => typeof value === "string");

  console.log(JSON.stringify({
    oauth: "dcr+pkce+bearer",
    protocol: MODERN_MCP_PROTOCOL_VERSION,
    connection_state: status.connection_state,
    active_transport: status.active_transport ?? null,
    observation_source: observed.source ?? null,
    observation_element_count: elements.length,
    act_backend: action.backend ?? null,
    act_count: action.count ?? null,
    calculator_display: displayValues,
  }));
} finally {
  await new Promise<void>((resolve) => listener.close(() => resolve()));
  await app.close();
  rmSync(stateDirectory, { recursive: true, force: true });
}

async function tool(token: string, name: string, args: Record<string, unknown>): Promise<Record<string, unknown>> {
  const response = await fetch(`${base}/mcp`, {
    method: "POST",
    headers: {
      "authorization": `Bearer ${token}`,
      "content-type": "application/json",
      "mcp-method": "tools/call",
      "mcp-name": name,
      "mcp-protocol-version": MODERN_MCP_PROTOCOL_VERSION,
    },
    body: JSON.stringify({
      jsonrpc: "2.0",
      id: `http-smoke-${name}`,
      method: "tools/call",
      params: {
        name,
        arguments: args,
        _meta: {
          "io.modelcontextprotocol/protocolVersion": MODERN_MCP_PROTOCOL_VERSION,
          "io.modelcontextprotocol/clientCapabilities": {},
        },
      },
    }),
  });
  const body = await response.json() as { result?: { structuredContent?: Record<string, unknown>; isError?: boolean } };
  if (!response.ok || body.result?.isError || !body.result?.structuredContent) {
    throw new Error(`MCP ${name} failed (${response.status}): ${JSON.stringify(body)}`);
  }
  return body.result.structuredContent;
}

function isElement(value: unknown): value is ElementRecord {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

async function freePort(): Promise<number> {
  const probe = createServer();
  await new Promise<void>((resolve, reject) => {
    probe.once("error", reject);
    probe.listen(0, "127.0.0.1", () => resolve());
  });
  const address = probe.address();
  if (!address || typeof address === "string") throw new Error("Could not allocate loopback port");
  const result = address.port;
  await new Promise<void>((resolve) => probe.close(() => resolve()));
  return result;
}
