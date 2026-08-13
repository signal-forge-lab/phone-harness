import { homedir } from "node:os";
import { join } from "node:path";

export interface McpConfig {
  host: "127.0.0.1";
  port: number;
  publicBaseUrl: string;
  oauthIssuerUrl?: string;
  oauthResourceUrl?: string;
  stateDirectory: string;
  ownerToken: string;
  allowedRedirectHosts: string[];
  allowedOrigins: string[];
}

export function loadConfig(env: NodeJS.ProcessEnv = process.env): McpConfig {
  const publicBaseUrl = requiredUrl(env.PHONE_HARNESS_MCP_PUBLIC_BASE_URL, "PHONE_HARNESS_MCP_PUBLIC_BASE_URL");
  const oauthIssuerUrl = requiredUrl(env.PHONE_HARNESS_MCP_OAUTH_ISSUER_URL ?? publicBaseUrl, "PHONE_HARNESS_MCP_OAUTH_ISSUER_URL");
  const oauthResourceUrl = env.PHONE_HARNESS_MCP_OAUTH_RESOURCE_URL
    ? requiredUrl(env.PHONE_HARNESS_MCP_OAUTH_RESOURCE_URL, "PHONE_HARNESS_MCP_OAUTH_RESOURCE_URL")
    : undefined;
  const ownerToken = String(env.PHONE_HARNESS_MCP_OWNER_TOKEN ?? "");
  if (ownerToken.length < 16) throw new Error("PHONE_HARNESS_MCP_OWNER_TOKEN must be at least 16 characters");
  const port = Number(env.PHONE_HARNESS_MCP_PORT ?? 7677);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("PHONE_HARNESS_MCP_PORT must be a valid TCP port");
  const publicUrl = new URL(publicBaseUrl);
  const oauthIssuer = new URL(oauthIssuerUrl);
  return {
    host: "127.0.0.1",
    port,
    publicBaseUrl: publicUrl.href,
    oauthIssuerUrl: oauthIssuer.href,
    oauthResourceUrl,
    stateDirectory: env.PHONE_HARNESS_MCP_STATE_DIR || join(homedir(), ".phone-harness-mcp"),
    ownerToken,
    allowedRedirectHosts: stringList(env.PHONE_HARNESS_MCP_ALLOWED_REDIRECT_HOSTS, ["chatgpt.com"]),
    allowedOrigins: stringList(env.PHONE_HARNESS_MCP_ALLOWED_ORIGINS, Array.from(new Set([publicUrl.origin, oauthIssuer.origin, "https://chatgpt.com"]))),
  };
}

function requiredUrl(value: string | undefined, name: string): string {
  if (!value) throw new Error(`${name} is required`);
  const parsed = new URL(value);
  if (parsed.protocol !== "https:" && !["localhost", "127.0.0.1", "::1"].includes(parsed.hostname)) {
    throw new Error(`${name} must use HTTPS for non-loopback hosts`);
  }
  return parsed.href;
}

function stringList(value: string | undefined, fallback: string[]): string[] {
  return value ? value.split(",").map((item) => item.trim()).filter(Boolean) : fallback;
}
