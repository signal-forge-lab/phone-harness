import { mkdirSync, renameSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { performance } from "node:perf_hooks";

export interface RequestStatusToken {
  method: string;
  tool?: string;
  protocolVersion?: string;
  startedAt: number;
}

export class McpStatusWriter {
  private readonly path: string;
  private readonly startedAt = new Date().toISOString();
  private activeRequests = 0;
  private totalRequests = 0;
  private lastRequest?: Record<string, unknown>;
  private processState = "running";

  constructor(stateDirectory: string, private readonly publicBaseUrl: string, private readonly port: number) {
    mkdirSync(stateDirectory, { recursive: true });
    this.path = join(stateDirectory, "mcp-status.json");
    this.write();
  }

  begin(method: string, tool?: string, protocolVersion?: string): RequestStatusToken {
    this.activeRequests += 1;
    this.totalRequests += 1;
    const token = {
      method: safeMethod(method),
      tool: safeTool(tool),
      protocolVersion: protocolVersion === "2026-07-28" ? protocolVersion : undefined,
      startedAt: performance.now(),
    };
    this.write({ current_request: safeRequest(token) });
    return token;
  }

  finish(token: RequestStatusToken, status: number): void {
    this.activeRequests = Math.max(0, this.activeRequests - 1);
    this.lastRequest = {
      ...safeRequest(token),
      status,
      ok: status < 400,
      duration_ms: Math.round((performance.now() - token.startedAt) * 10) / 10,
      at: new Date().toISOString(),
    };
    this.write();
  }

  close(): void {
    this.processState = "stopped";
    this.write();
  }

  private write(extra: Record<string, unknown> = {}): void {
    const state = {
      schema_version: 1,
      pid: process.pid,
      started_at: this.startedAt,
      updated_at: new Date().toISOString(),
      process_state: this.processState,
      protocol_version: "2026-07-28",
      local_port: this.port,
      public_base_url: this.publicBaseUrl,
      active_requests: this.activeRequests,
      total_requests: this.totalRequests,
      last_request: this.lastRequest ?? null,
      ...extra,
    };
    const temporary = `${this.path}.${process.pid}.tmp`;
    writeFileSync(temporary, `${JSON.stringify(state)}\n`, { encoding: "utf8", mode: 0o600 });
    renameSync(temporary, this.path);
  }
}

function safeRequest(token: Pick<RequestStatusToken, "method" | "tool" | "protocolVersion">): Record<string, unknown> {
  return {
    method: token.method,
    ...(token.tool ? { tool: token.tool } : {}),
    ...(token.protocolVersion ? { protocol_version: token.protocolVersion } : {}),
  };
}

function safeMethod(value: string): string {
  return ["server/discover", "tools/list", "tools/call"].includes(value) ? value : "other";
}

function safeTool(value: string | undefined): string | undefined {
  return value && ["phone_status", "phone_observe", "phone_act"].includes(value) ? value : undefined;
}
