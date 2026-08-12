import { loadConfig } from "./config.js";
import { createHttpServer } from "./http-server.js";
import { PythonRuntimeBridge } from "./runtime-bridge.js";
import { join } from "node:path";

const config = loadConfig();
process.env.PHONE_HARNESS_STATUS_FILE ||= join(config.stateDirectory, "runtime-status.json");
const runtime = new PythonRuntimeBridge();
const server = createHttpServer(config, runtime);
const listener = server.app.listen(config.port, config.host, () => {
  console.error(`phone-harness MCP listening on http://${config.host}:${config.port}/mcp`);
});

let closing = false;
async function shutdown(): Promise<void> {
  if (closing) return;
  closing = true;
  await new Promise<void>((resolve) => listener.close(() => resolve()));
  await server.close();
}

process.on("SIGINT", () => void shutdown().then(() => process.exit(0)));
process.on("SIGTERM", () => void shutdown().then(() => process.exit(0)));
