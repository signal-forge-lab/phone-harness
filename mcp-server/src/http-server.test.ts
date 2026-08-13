import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { Server } from "node:http";
import test from "node:test";
import { createHttpServer } from "./http-server.js";
import type { McpConfig } from "./config.js";
import type { RuntimeBridge } from "./runtime-bridge.js";

class NoopRuntime implements RuntimeBridge {
  async call(): Promise<Record<string, unknown>> {
    return { contract_version: 2, connection_state: "ready" };
  }
  async close(): Promise<void> {}
}

test("HTTP boundary publishes OAuth protected-resource metadata and rejects unauthenticated MCP", async (t) => {
  const fixture = await startFixture();
  t.after(() => fixture.close());

  const metadata = await fetch(`${fixture.base}/.well-known/oauth-protected-resource/mcp`);
  assert.equal(metadata.status, 200);
  const body = await metadata.json() as { resource?: string; authorization_servers?: string[] };
  assert.equal(body.resource, "https://phone.example.test:8443/mcp");
  assert.ok(body.authorization_servers?.includes("https://auth.example.test/phone-auth/"));

  const authMetadata = await fetch(`${fixture.base}/.well-known/oauth-authorization-server`);
  assert.equal(authMetadata.status, 200);
  const authBody = await authMetadata.json() as {
    issuer?: string;
    authorization_endpoint?: string;
    token_endpoint?: string;
    registration_endpoint?: string;
  };
  assert.equal(authBody.issuer, "https://auth.example.test/phone-auth/");
  assert.equal(authBody.authorization_endpoint, "https://auth.example.test/phone-auth/authorize");
  assert.equal(authBody.token_endpoint, "https://auth.example.test/phone-auth/token");
  assert.equal(authBody.registration_endpoint, "https://auth.example.test/phone-auth/register");

  const denied = await fetch(`${fixture.base}/mcp`, {
    method: "POST",
    headers: { "content-type": "application/json", "mcp-protocol-version": "2026-07-28" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "server/discover", params: {} }),
  });
  assert.equal(denied.status, 401);
  assert.match(denied.headers.get("www-authenticate") ?? "", /resource_metadata/i);
});

test("HTTP boundary rejects an untrusted Origin before OAuth", async (t) => {
  const fixture = await startFixture();
  t.after(() => fixture.close());
  const response = await fetch(`${fixture.base}/mcp`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "origin": "https://evil.example",
    },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "server/discover", params: {} }),
  });
  assert.equal(response.status, 403);
});

test("health endpoint contains no phone or auth state", async (t) => {
  const fixture = await startFixture();
  t.after(() => fixture.close());
  const response = await fetch(`${fixture.base}/healthz`);
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { ok: true, name: "phone-harness-mcp" });
});

test("HTTP boundary trusts only loopback proxies", async (t) => {
  const fixture = await startFixture();
  t.after(() => fixture.close());
  assert.equal(fixture.appSetting("trust proxy"), "loopback");
});

test("MCP status snapshot records only request identity and never tool arguments", async (t) => {
  const fixture = await startFixture();
  t.after(() => fixture.close());
  await fetch(`${fixture.base}/mcp`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "mcp-method": "tools/call",
      "mcp-name": "phone_act",
      "mcp-protocol-version": "2026-07-28",
      "x-private-noise": "PRIVATE HEADER",
    },
    body: JSON.stringify({
      jsonrpc: "2.0",
      id: 1,
      method: "tools/call",
      params: { name: "phone_act", arguments: { actions: [{ op: "type_text", text: "PRIVATE TEXT" }] } },
    }),
  });
  const raw = readFileSync(join(fixture.stateDirectory, "mcp-status.json"), "utf8");
  assert.doesNotMatch(raw, /PRIVATE TEXT/);
  assert.doesNotMatch(raw, /PRIVATE HEADER/);
  const state = JSON.parse(raw) as { last_request?: { method?: string; tool?: string; protocol_version?: string } };
  assert.equal(state.last_request?.method, "tools/call");
  assert.equal(state.last_request?.tool, "phone_act");
  assert.equal(state.last_request?.protocol_version, "2026-07-28");
});

test("DCR plus PKCE authorization code flow yields a bearer token accepted by modern MCP", async (t) => {
  const fixture = await startFixture();
  t.after(() => fixture.close());
  const redirectUri = "http://127.0.0.1:45678/callback";
  const registeredResponse = await fetch(`${fixture.base}/register`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      client_name: "phone-harness-test",
      redirect_uris: [redirectUri],
      token_endpoint_auth_method: "none",
      grant_types: ["authorization_code", "refresh_token"],
      response_types: ["code"],
    }),
  });
  assert.equal(registeredResponse.status, 201, await registeredResponse.clone().text());
  const registered = await registeredResponse.json() as { client_id: string };

  const verifier = "phone-harness-modern-test-verifier-012345678901234567890123456789";
  const challenge = createHash("sha256").update(verifier).digest("base64url");
  const authorize = new URL(`${fixture.base}/authorize`);
  const authFields = {
    response_type: "code",
    client_id: registered.client_id,
    redirect_uri: redirectUri,
    code_challenge: challenge,
    code_challenge_method: "S256",
    scope: "phone",
    state: "state-1",
    resource: "https://phone.example.test:8443/mcp",
  };
  for (const [key, value] of Object.entries(authFields)) authorize.searchParams.set(key, value);
  const consent = await fetch(authorize, { redirect: "manual" });
  assert.equal(consent.status, 200);

  const approved = await fetch(`${fixture.base}/authorize`, {
    method: "POST",
    redirect: "manual",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ ...authFields, owner_token: "0123456789abcdef0123456789abcdef" }),
  });
  assert.equal(approved.status, 302, await approved.clone().text());
  const location = new URL(approved.headers.get("location") ?? "");
  const code = location.searchParams.get("code");
  assert.ok(code);
  assert.equal(location.searchParams.get("state"), "state-1");

  const tokenResponse = await fetch(`${fixture.base}/token`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      code,
      code_verifier: verifier,
      client_id: registered.client_id,
      redirect_uri: redirectUri,
      resource: "https://phone.example.test:8443/mcp",
    }),
  });
  assert.equal(tokenResponse.status, 200, await tokenResponse.clone().text());
  const tokens = await tokenResponse.json() as { access_token: string; refresh_token?: string };
  assert.ok(tokens.access_token);
  assert.ok(tokens.refresh_token);

  const modern = await fetch(`${fixture.base}/mcp`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "authorization": `Bearer ${tokens.access_token}`,
      "mcp-method": "server/discover",
      "mcp-protocol-version": "2026-07-28",
    },
    body: JSON.stringify({
      jsonrpc: "2.0",
      id: "oauth-modern",
      method: "server/discover",
      params: {
        _meta: {
          "io.modelcontextprotocol/protocolVersion": "2026-07-28",
          "io.modelcontextprotocol/clientCapabilities": {},
        },
      },
    }),
  });
  assert.equal(modern.status, 200, await modern.clone().text());
  const modernBody = await modern.json() as { result?: { supportedVersions?: string[] } };
  assert.ok(modernBody.result?.supportedVersions?.includes("2026-07-28"));
});

async function startFixture(): Promise<{
  base: string;
  stateDirectory: string;
  appSetting(name: string): unknown;
  close(): Promise<void>;
}> {
  const stateDirectory = mkdtempSync(join(tmpdir(), "phone-harness-mcp-http-"));
  const config: McpConfig = {
    host: "127.0.0.1",
    port: 0,
    publicBaseUrl: "https://phone.example.test:8443/",
    oauthIssuerUrl: "https://auth.example.test/phone-auth/",
    oauthResourceUrl: "https://tunnel.example.test/v1/mcp/tunnel-test",
    stateDirectory,
    ownerToken: "0123456789abcdef0123456789abcdef",
    allowedRedirectHosts: ["chatgpt.com"],
    allowedOrigins: ["https://phone.example.test:8443", "https://chatgpt.com"],
  };
  const runtime = new NoopRuntime();
  const app = createHttpServer(config, runtime);
  const listener = await new Promise<Server>((resolve) => {
    const server = app.app.listen(0, "127.0.0.1", () => resolve(server));
  });
  const address = listener.address();
  if (!address || typeof address === "string") throw new Error("missing test listener address");
  return {
    base: `http://127.0.0.1:${address.port}`,
    stateDirectory,
    appSetting: (name: string) => app.app.get(name),
    close: async () => {
      await new Promise<void>((resolve) => listener.close(() => resolve()));
      await app.close();
      rmSync(stateDirectory, { recursive: true, force: true });
    },
  };
}
