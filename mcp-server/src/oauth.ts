import { createHash, randomBytes, randomUUID, timingSafeEqual } from "node:crypto";
import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { Response } from "express";
import type { OAuthRegisteredClientsStore } from "@modelcontextprotocol/sdk/server/auth/clients.js";
import { AccessDeniedError, InvalidGrantError, InvalidRequestError, InvalidTokenError } from "@modelcontextprotocol/sdk/server/auth/errors.js";
import type { AuthorizationParams, OAuthServerProvider } from "@modelcontextprotocol/sdk/server/auth/provider.js";
import type { AuthInfo } from "@modelcontextprotocol/sdk/server/auth/types.js";
import type { OAuthClientInformationFull, OAuthTokenRevocationRequest, OAuthTokens } from "@modelcontextprotocol/sdk/shared/auth.js";
import { checkResourceAllowed, resourceUrlFromServerUrl } from "@modelcontextprotocol/sdk/shared/auth-utils.js";

interface TokenRecord {
  clientId: string;
  scopes: string[];
  expiresAt: number;
  resource?: string;
}

interface PersistedOAuthState {
  clients: Record<string, OAuthClientInformationFull>;
  accessTokens: Record<string, TokenRecord>;
  refreshTokens: Record<string, TokenRecord>;
}

export class JsonOAuthStore {
  private readonly path: string;
  private state: PersistedOAuthState;

  constructor(stateDirectory: string) {
    mkdirSync(stateDirectory, { recursive: true });
    this.path = join(stateDirectory, "oauth-state.json");
    this.state = this.load();
    this.pruneExpired();
  }

  getClient(clientId: string): OAuthClientInformationFull | undefined {
    return this.state.clients[clientId];
  }

  registerClient(
    client: Omit<OAuthClientInformationFull, "client_id" | "client_id_issued_at">,
    allowedRedirectHosts: readonly string[],
    maxClients = 50,
  ): OAuthClientInformationFull {
    if (!client.redirect_uris.every((uri) => isAllowedOAuthRedirectUri(String(uri), allowedRedirectHosts))) {
      throw new InvalidRequestError("OAuth redirect URI is not allowed");
    }
    if (Object.keys(this.state.clients).length >= maxClients) {
      throw new InvalidRequestError("OAuth client registration limit reached");
    }
    const registered: OAuthClientInformationFull = {
      ...client,
      client_id: `phone-${randomUUID()}`,
      client_id_issued_at: Math.floor(Date.now() / 1_000),
      token_endpoint_auth_method: client.token_endpoint_auth_method ?? "none",
      grant_types: client.grant_types ?? ["authorization_code", "refresh_token"],
      response_types: client.response_types ?? ["code"],
    };
    this.state.clients[registered.client_id] = registered;
    this.persist();
    return registered;
  }

  saveAccessToken(hash: string, record: TokenRecord): void {
    this.state.accessTokens[hash] = record;
    this.persist();
  }

  getAccessToken(hash: string): TokenRecord | undefined {
    return this.state.accessTokens[hash];
  }

  deleteAccessToken(hash: string): void {
    delete this.state.accessTokens[hash];
    this.persist();
  }

  saveRefreshToken(hash: string, record: TokenRecord): void {
    this.state.refreshTokens[hash] = record;
    this.persist();
  }

  getRefreshToken(hash: string): TokenRecord | undefined {
    return this.state.refreshTokens[hash];
  }

  deleteRefreshToken(hash: string): void {
    delete this.state.refreshTokens[hash];
    this.persist();
  }

  replaceRefreshToken(oldHash: string | undefined, accessHash: string, access: TokenRecord, refreshHash: string, refresh: TokenRecord): boolean {
    if (oldHash && !this.state.refreshTokens[oldHash]) return false;
    if (oldHash) delete this.state.refreshTokens[oldHash];
    this.state.accessTokens[accessHash] = access;
    this.state.refreshTokens[refreshHash] = refresh;
    this.persist();
    return true;
  }

  private load(): PersistedOAuthState {
    try {
      const parsed = JSON.parse(readFileSync(this.path, "utf8")) as Partial<PersistedOAuthState>;
      return {
        clients: objectRecord(parsed.clients),
        accessTokens: objectRecord(parsed.accessTokens),
        refreshTokens: objectRecord(parsed.refreshTokens),
      } as PersistedOAuthState;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
      return { clients: {}, accessTokens: {}, refreshTokens: {} };
    }
  }

  private pruneExpired(): void {
    const now = Math.floor(Date.now() / 1_000);
    for (const [hash, record] of Object.entries(this.state.accessTokens)) {
      if (record.expiresAt < now) delete this.state.accessTokens[hash];
    }
    for (const [hash, record] of Object.entries(this.state.refreshTokens)) {
      if (record.expiresAt < now) delete this.state.refreshTokens[hash];
    }
    this.persist();
  }

  private persist(): void {
    const temporary = `${this.path}.${process.pid}.tmp`;
    writeFileSync(temporary, `${JSON.stringify(this.state)}\n`, { encoding: "utf8", mode: 0o600 });
    renameSync(temporary, this.path);
  }
}

class JsonOAuthClientsStore implements OAuthRegisteredClientsStore {
  constructor(
    private readonly store: JsonOAuthStore,
    private readonly allowedRedirectHosts: readonly string[],
  ) {}

  getClient(clientId: string): OAuthClientInformationFull | undefined {
    return this.store.getClient(clientId);
  }

  registerClient(client: Omit<OAuthClientInformationFull, "client_id" | "client_id_issued_at">): OAuthClientInformationFull {
    return this.store.registerClient(client, this.allowedRedirectHosts);
  }
}

interface AuthorizationCodeRecord {
  clientId: string;
  params: AuthorizationParams;
  expiresAtMs: number;
}

export interface SingleUserOAuthConfig {
  ownerToken: string;
  scopes: string[];
  allowedRedirectHosts: string[];
  accessTokenTtlSeconds?: number;
  refreshTokenTtlSeconds?: number;
}

export class SingleUserOAuthProvider implements OAuthServerProvider {
  readonly clientsStore: OAuthRegisteredClientsStore;
  private readonly codes = new Map<string, AuthorizationCodeRecord>();
  private readonly store: JsonOAuthStore;
  private readonly resourceServerUrl: URL;
  private readonly accessTokenTtlSeconds: number;
  private readonly refreshTokenTtlSeconds: number;

  constructor(
    private readonly config: SingleUserOAuthConfig,
    resourceServerUrl: URL,
    stateDirectory: string,
    private readonly additionalResourceUrl?: URL,
  ) {
    this.resourceServerUrl = resourceUrlFromServerUrl(resourceServerUrl);
    this.store = new JsonOAuthStore(stateDirectory);
    this.clientsStore = new JsonOAuthClientsStore(this.store, config.allowedRedirectHosts);
    this.accessTokenTtlSeconds = config.accessTokenTtlSeconds ?? 3_600;
    this.refreshTokenTtlSeconds = config.refreshTokenTtlSeconds ?? 30 * 24 * 60 * 60;
  }

  async authorize(client: OAuthClientInformationFull, params: AuthorizationParams, res: Response): Promise<void> {
    if (!params.resource || !this.isAllowedResource(params.resource)) {
      throw new InvalidRequestError("Invalid or missing OAuth resource");
    }
    if (!(params.scopes ?? []).every((scope) => this.config.scopes.includes(scope))) {
      throw new InvalidRequestError("Requested scope is not supported");
    }
    if (res.req.method !== "POST") {
      res.status(200).type("html").send(authorizationForm(client, params));
      return;
    }
    const provided = String(res.req.body?.owner_token ?? "");
    if (!safeEquals(provided, this.config.ownerToken)) {
      await new Promise((resolve) => setTimeout(resolve, 500));
      res.status(401).type("html").send(authorizationForm(client, params, "Owner password was not accepted."));
      return;
    }
    this.pruneCodes();
    const code = `code-${randomUUID()}`;
    this.codes.set(code, { clientId: client.client_id, params, expiresAtMs: Date.now() + 5 * 60_000 });
    const redirect = new URL(params.redirectUri);
    redirect.searchParams.set("code", code);
    if (params.state !== undefined) redirect.searchParams.set("state", params.state);
    res.redirect(302, redirect.href);
  }

  async challengeForAuthorizationCode(client: OAuthClientInformationFull, authorizationCode: string): Promise<string> {
    return this.validCode(client, authorizationCode).params.codeChallenge;
  }

  async exchangeAuthorizationCode(
    client: OAuthClientInformationFull,
    authorizationCode: string,
    _codeVerifier?: string,
    redirectUri?: string,
    resource?: URL,
  ): Promise<OAuthTokens> {
    const record = this.validCode(client, authorizationCode);
    if (redirectUri && redirectUri !== record.params.redirectUri) throw new InvalidGrantError("redirect_uri mismatch");
    if (resource && !this.isAllowedResource(resource)) {
      throw new InvalidGrantError("Invalid resource");
    }
    this.codes.delete(authorizationCode);
    return this.issueTokens(client.client_id, record.params.scopes ?? this.config.scopes, record.params.resource);
  }

  async exchangeRefreshToken(client: OAuthClientInformationFull, refreshToken: string, scopes?: string[], resource?: URL): Promise<OAuthTokens> {
    const refreshHash = hashToken(refreshToken);
    const record = this.store.getRefreshToken(refreshHash);
    const now = Math.floor(Date.now() / 1_000);
    if (!record || record.clientId !== client.client_id || record.expiresAt < now) throw new InvalidGrantError("Invalid refresh token");
    if (resource && !this.isAllowedResource(resource)) {
      throw new InvalidGrantError("Invalid resource");
    }
    const requestedScopes = scopes ?? record.scopes;
    if (!requestedScopes.every((scope) => record.scopes.includes(scope))) throw new AccessDeniedError("Refresh token cannot grant requested scopes");
    return this.issueTokens(client.client_id, requestedScopes, resource ?? (record.resource ? new URL(record.resource) : undefined), refreshHash);
  }

  async verifyAccessToken(token: string): Promise<AuthInfo> {
    const record = this.store.getAccessToken(hashToken(token));
    if (!record || record.expiresAt < Math.floor(Date.now() / 1_000)) throw new InvalidTokenError("Invalid or expired access token");
    return {
      token,
      clientId: record.clientId,
      scopes: record.scopes,
      expiresAt: record.expiresAt,
      resource: record.resource ? new URL(record.resource) : undefined,
    };
  }

  async revokeToken(_client: OAuthClientInformationFull, request: OAuthTokenRevocationRequest): Promise<void> {
    const hash = hashToken(request.token);
    this.store.deleteAccessToken(hash);
    this.store.deleteRefreshToken(hash);
  }

  isAllowedResource(resource: URL): boolean {
    if (checkResourceAllowed({ requestedResource: resource, configuredResource: this.resourceServerUrl })) return true;
    return this.additionalResourceUrl
      ? checkResourceAllowed({ requestedResource: resource, configuredResource: this.additionalResourceUrl })
      : false;
  }

  private validCode(client: OAuthClientInformationFull, code: string): AuthorizationCodeRecord {
    this.pruneCodes();
    const record = this.codes.get(code);
    if (!record || record.clientId !== client.client_id || record.expiresAtMs < Date.now()) throw new InvalidGrantError("Invalid authorization code");
    return record;
  }

  private pruneCodes(): void {
    const now = Date.now();
    for (const [code, record] of this.codes) if (record.expiresAtMs < now) this.codes.delete(code);
  }

  private issueTokens(clientId: string, scopes: string[], resource?: URL, consumedRefreshHash?: string): OAuthTokens {
    const now = Math.floor(Date.now() / 1_000);
    const access = randomToken();
    const refresh = randomToken();
    const saved = this.store.replaceRefreshToken(
      consumedRefreshHash,
      hashToken(access),
      { clientId, scopes, expiresAt: now + this.accessTokenTtlSeconds, resource: resource?.href },
      hashToken(refresh),
      { clientId, scopes, expiresAt: now + this.refreshTokenTtlSeconds, resource: resource?.href },
    );
    if (!saved) throw new InvalidGrantError("Invalid refresh token");
    return {
      access_token: access,
      token_type: "bearer",
      expires_in: this.accessTokenTtlSeconds,
      refresh_token: refresh,
      scope: scopes.join(" "),
    };
  }
}

export function isAllowedOAuthRedirectUri(redirectUri: string, allowedHosts: readonly string[]): boolean {
  let parsed: URL;
  try {
    parsed = new URL(redirectUri);
  } catch {
    return false;
  }
  if (parsed.username || parsed.password || parsed.hash) return false;
  const host = parsed.hostname.toLowerCase();
  if (["localhost", "127.0.0.1", "::1", "[::1]"].includes(host)) return parsed.protocol === "http:" || parsed.protocol === "https:";
  return parsed.protocol === "https:" && allowedHosts.some((allowed) => allowed.toLowerCase() === host);
}

function authorizationForm(client: OAuthClientInformationFull, params: AuthorizationParams, error?: string): string {
  const fields: Record<string, string | undefined> = {
    response_type: "code",
    client_id: client.client_id,
    redirect_uri: params.redirectUri,
    code_challenge: params.codeChallenge,
    code_challenge_method: "S256",
    scope: params.scopes?.join(" "),
    state: params.state,
    resource: params.resource?.href,
  };
  const hidden = Object.entries(fields)
    .filter((entry): entry is [string, string] => entry[1] !== undefined)
    .map(([name, value]) => `<input type="hidden" name="${escapeHtml(name)}" value="${escapeHtml(value)}">`)
    .join("");
  return `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Connect phone-harness</title></head><body><main><h1>Connect phone-harness</h1><p>Approve only your own MCP client.</p>${error ? `<p>${escapeHtml(error)}</p>` : ""}<p>Client: ${escapeHtml(client.client_name ?? client.client_id)}</p><form method="post">${hidden}<label>Owner password <input name="owner_token" type="password" autocomplete="current-password" required></label><button type="submit">Authorize</button></form></main></body></html>`;
}

function randomToken(): string {
  return randomBytes(32).toString("base64url");
}

function hashToken(token: string): string {
  return createHash("sha256").update(token).digest("base64url");
}

function safeEquals(left: string, right: string): boolean {
  const a = Buffer.from(left);
  const b = Buffer.from(right);
  return a.length === b.length && timingSafeEqual(a, b);
}

function escapeHtml(value: string): string {
  return value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}

function objectRecord(value: unknown): Record<string, any> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, any> : {};
}
