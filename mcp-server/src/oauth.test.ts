import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { JsonOAuthStore, isAllowedOAuthRedirectUri } from "./oauth.js";

test("OAuth redirect policy allows ChatGPT and loopback but rejects insecure remote redirects", () => {
  const allowed = ["chatgpt.com"];
  assert.equal(isAllowedOAuthRedirectUri("https://chatgpt.com/connector_platform_oauth_redirect", allowed), true);
  assert.equal(isAllowedOAuthRedirectUri("http://localhost:4567/callback", allowed), true);
  assert.equal(isAllowedOAuthRedirectUri("http://127.0.0.1:4567/callback", allowed), true);
  assert.equal(isAllowedOAuthRedirectUri("http://chatgpt.com/callback", allowed), false);
  assert.equal(isAllowedOAuthRedirectUri("https://evil.example/callback", allowed), false);
});

test("OAuth store persists client metadata and only hashed token keys", () => {
  const directory = mkdtempSync(join(tmpdir(), "phone-harness-mcp-oauth-"));
  try {
    const store = new JsonOAuthStore(directory);
    const client = store.registerClient({ redirect_uris: ["https://chatgpt.com/callback"] }, ["chatgpt.com"]);
    store.saveAccessToken("hash-only", {
      clientId: client.client_id,
      scopes: ["phone"],
      expiresAt: 4_000_000_000,
      resource: "https://phone.example/mcp",
    });
    const reopened = new JsonOAuthStore(directory);
    assert.equal(reopened.getClient(client.client_id)?.client_id, client.client_id);
    assert.equal(reopened.getAccessToken("hash-only")?.clientId, client.client_id);
    assert.equal(reopened.getAccessToken("raw-secret-token"), undefined);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});
