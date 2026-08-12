import { createMcpExpressApp } from "@modelcontextprotocol/sdk/server/express.js";
import { getOAuthProtectedResourceMetadataUrl, mcpAuthRouter } from "@modelcontextprotocol/sdk/server/auth/router.js";
import { requireBearerAuth } from "@modelcontextprotocol/sdk/server/auth/middleware/bearerAuth.js";
import { checkResourceAllowed, resourceUrlFromServerUrl } from "@modelcontextprotocol/sdk/shared/auth-utils.js";
import { toNodeHandler } from "@modelcontextprotocol/node";
import type { McpConfig } from "./config.js";
import { SingleUserOAuthProvider } from "./oauth.js";
import type { RuntimeBridge } from "./runtime-bridge.js";
import { createPhoneMcpHandler } from "./server.js";
import { McpStatusWriter } from "./mcp-status.js";

export function createHttpServer(config: McpConfig, runtime: RuntimeBridge) {
  const publicBase = new URL(config.publicBaseUrl);
  const mcpUrl = new URL("/mcp", publicBase);
  const resourceServerUrl = resourceUrlFromServerUrl(mcpUrl);
  const allowedHosts = Array.from(new Set([publicBase.hostname, "127.0.0.1", "localhost"]));
  const app = createMcpExpressApp({ host: config.host, allowedHosts });
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
  );
  app.use(mcpAuthRouter({
    provider: oauth,
    issuerUrl: publicBase,
    baseUrl: publicBase,
    resourceServerUrl,
    scopesSupported: ["phone"],
    resourceName: "phone-harness",
  }));

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
    if (!req.auth?.resource || !checkResourceAllowed({ requestedResource: req.auth.resource, configuredResource: resourceServerUrl })) {
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
