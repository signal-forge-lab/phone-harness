import { createMcpExpressApp } from "@modelcontextprotocol/sdk/server/express.js";
import { createOAuthMetadata, getOAuthProtectedResourceMetadataUrl, mcpAuthRouter } from "@modelcontextprotocol/sdk/server/auth/router.js";
import { requireBearerAuth } from "@modelcontextprotocol/sdk/server/auth/middleware/bearerAuth.js";
import { resourceUrlFromServerUrl } from "@modelcontextprotocol/sdk/shared/auth-utils.js";
import { toNodeHandler } from "@modelcontextprotocol/node";
import type { McpConfig } from "./config.js";
import { SingleUserOAuthProvider } from "./oauth.js";
import type { RuntimeBridge } from "./runtime-bridge.js";
import { createPhoneMcpHandler } from "./server.js";
import { McpStatusWriter } from "./mcp-status.js";

export function createHttpServer(config: McpConfig, runtime: RuntimeBridge) {
  const publicBase = new URL(config.publicBaseUrl);
  const oauthIssuer = new URL(config.oauthIssuerUrl ?? config.publicBaseUrl);
  const mcpUrl = new URL("/mcp", publicBase);
  const localResourceServerUrl = resourceUrlFromServerUrl(mcpUrl);
  const resourceServerUrl = resourceUrlFromServerUrl(config.oauthResourceUrl ? new URL(config.oauthResourceUrl) : mcpUrl);
  const allowedHosts = Array.from(new Set([publicBase.hostname, oauthIssuer.hostname, "127.0.0.1", "localhost"]));
  const app = createMcpExpressApp({ host: config.host, allowedHosts });
  app.set("trust proxy", "loopback");
  app.disable("x-powered-by");
  const status = new McpStatusWriter(config.stateDirectory, config.publicBaseUrl, config.port);

  app.use((req, res, next) => {
    if (req.path !== "/mcp") {
      next();
      return;
    }
    const token = status.begin(
      req.header("mcp-method") ?? req.method,
      req.header("mcp-name") ?? undefined,
      req.header("mcp-protocol-version") ?? undefined,
    );
    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      status.finish(token, res.statusCode);
    };
    res.once("finish", finish);
    res.once("close", finish);
    next();
  });

  app.use((req, res, next) => {
    const origin = req.header("origin");
    if (origin && !config.allowedOrigins.includes(origin)) {
      res.status(403).json({ error: "Forbidden origin" });
      return;
    }
    next();
  });

  const oauth = new SingleUserOAuthProvider(
    { ownerToken: config.ownerToken, scopes: ["phone"], allowedRedirectHosts: config.allowedRedirectHosts },
    mcpUrl,
    config.stateDirectory,
    config.oauthResourceUrl ? new URL(config.oauthResourceUrl) : undefined,
  );
  const oauthOptions = {
    provider: oauth,
    issuerUrl: oauthIssuer,
    baseUrl: oauthIssuer,
    resourceServerUrl,
    scopesSupported: ["phone"],
    resourceName: "phone-harness",
  };
  const generatedOAuthMetadata = createOAuthMetadata(oauthOptions);
  const oauthMetadata = {
    ...generatedOAuthMetadata,
    authorization_endpoint: new URL("authorize", oauthIssuer).href,
    token_endpoint: new URL("token", oauthIssuer).href,
    registration_endpoint: generatedOAuthMetadata.registration_endpoint ? new URL("register", oauthIssuer).href : undefined,
    revocation_endpoint: generatedOAuthMetadata.revocation_endpoint ? new URL("revoke", oauthIssuer).href : undefined,
  };
  app.get("/.well-known/oauth-authorization-server", (_req, res) => res.json(oauthMetadata));
  if (resourceServerUrl.href !== localResourceServerUrl.href) {
    const localMetadataPath = new URL(getOAuthProtectedResourceMetadataUrl(localResourceServerUrl)).pathname;
    app.get(localMetadataPath, (_req, res) => res.json({
      resource: localResourceServerUrl.href,
      authorization_servers: [oauthIssuer.href],
      scopes_supported: ["phone"],
      resource_name: "phone-harness",
    }));
  }
  app.use(mcpAuthRouter(oauthOptions));

  app.get("/healthz", (_req, res) => res.json({ ok: true, name: "phone-harness-mcp" }));

  const bearerAuth = requireBearerAuth({
    verifier: oauth,
    requiredScopes: ["phone"],
    resourceMetadataUrl: getOAuthProtectedResourceMetadataUrl(resourceServerUrl),
  });
  const modern = createPhoneMcpHandler(runtime);
  const nodeHandler = toNodeHandler(modern, {
    onerror: () => undefined,
  });

  app.all("/mcp", async (req, res) => {
    await new Promise<void>((resolve, reject) => {
      bearerAuth(req, res, (error?: unknown) => error ? reject(error) : resolve());
    });
    if (res.headersSent) return;
    if (!req.auth?.resource || !oauth.isAllowedResource(req.auth.resource)) {
      res.status(401).json({ jsonrpc: "2.0", error: { code: -32001, message: "Unauthorized" }, id: null });
      return;
    }
    await nodeHandler(req, res, req.body);
  });

  return {
    app,
    close: async () => {
      await modern.close();
      await runtime.close();
      status.close();
    },
  };
}
