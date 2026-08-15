import { createMcpHandler, McpServer } from "@modelcontextprotocol/server";
import * as z from "zod/v4";
import type { RuntimeBridge } from "./runtime-bridge.js";
import { RuntimeBridgeError } from "./runtime-bridge.js";

export const MODERN_MCP_PROTOCOL_VERSION = "2026-07-28";

const actionSchema = z.discriminatedUnion("op", [
  z.object({ op: z.literal("tap_text"), text: z.string().min(1), exact: z.boolean().optional(), index: z.number().int().nonnegative().optional() }),
  z.object({ op: z.literal("tap_element"), element_ref: z.string().min(1) }),
  z.object({ op: z.literal("tap"), x: z.number(), y: z.number() }),
  z.object({ op: z.literal("drag"), x1: z.number(), y1: z.number(), x2: z.number(), y2: z.number(), duration: z.number().positive().optional() }),
  z.object({ op: z.literal("type_text"), text: z.string() }),
  z.object({ op: z.literal("open_app"), name: z.string().min(1) }),
  z.object({ op: z.literal("home") }),
  z.object({ op: z.literal("swipe"), direction: z.enum(["up", "down", "left", "right"]), distance: z.number().positive().max(1).optional() }),
  z.object({ op: z.literal("scroll"), amount: z.number().optional() }),
  z.object({ op: z.literal("press"), combo: z.string().min(1) }),
  z.object({ op: z.literal("wait_stable"), timeout: z.number().positive().optional(), interval: z.number().positive().optional(), settle: z.number().int().positive().optional() }),
]);

const visualGridSchema = z.object({
  rows: z.number().int().positive().max(50),
  columns: z.number().int().positive().max(50),
  bounds: z.object({
    x: z.number().nonnegative(),
    y: z.number().nonnegative(),
    w: z.number().positive(),
    h: z.number().positive(),
  }),
  inset: z.number().min(0).max(0.49).optional(),
  exact_threshold: z.number().positive().max(1).optional(),
  min_content_score: z.number().min(0).max(255).optional(),
}).refine((grid) => grid.rows * grid.columns <= 400, {
  message: "visual_grid may contain at most 400 cells",
});

export function createPhoneMcpServer(runtime: RuntimeBridge): McpServer {
  const server = new McpServer(
    { name: "phone-harness", version: "0.1.0" },
    { capabilities: { tools: {} } },
  );

  server.registerTool(
    "phone_status",
    {
      description: "Read phone-harness runtime, connection, transport, tunnel, and backend health without changing the phone.",
      inputSchema: {},
      annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    },
    async () => toolResult(() => runtime.call("status", {})),
  );

  server.registerTool(
    "phone_observe",
    {
      description: "Observe the current iPhone screen. Uses accessibility first and OCR fallback. Returns temporary element_ref values for precise follow-up actions and hides document/editable text bodies by default. For games/canvas-style regular boards, request an image first to infer screenshot-pixel board bounds, then use visual_grid to obtain stable cell centers and conservative same-looking candidates. Treat those as visual candidates only and verify task/game semantics before mutating.",
      inputSchema: {
        force: z.boolean().optional().describe("Force a fresh observation instead of using the short-lived runtime cache."),
        include_image: z.boolean().optional().describe("Also return the current screenshot as image content. Defaults to false."),
        include_text_content: z.boolean().optional().describe("Include full document/editable text content. Keep false unless the task specifically requires reading it."),
        visual_grid: visualGridSchema.optional().describe("Analyze a regular visual board inside screenshot pixel bounds. Returns per-cell centers, signatures, perceptual hashes, pair similarity scores, and conservative exact-looking groups for vision-driven manipulation."),
      },
      annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    },
    async ({ force, include_image, include_text_content, visual_grid }) => toolResult(
      () => runtime.call("observe", {
        force: force ?? false,
        include_image: include_image ?? false,
        include_text_content: include_text_content ?? false,
        ...(visual_grid === undefined ? {} : { visual_grid }),
      }),
      true,
    ),
  );

  server.registerTool(
    "phone_act",
    {
      description: "Execute one bounded batch of phone actions. Use the observation_id returned by phone_observe for semantic or observation-derived actions; prefer tap_element with an element_ref when a precise observed target is available.",
      inputSchema: {
        observation_id: z.number().int().positive().optional(),
        actions: z.array(actionSchema).min(1).max(100),
      },
      annotations: { readOnlyHint: false, destructiveHint: true, idempotentHint: false, openWorldHint: true },
    },
    async ({ observation_id, actions }) => toolResult(() => runtime.call("act", {
      ...(observation_id === undefined ? {} : { observation_id }),
      actions,
    })),
  );

  return server;
}

export function createPhoneMcpHandler(runtime: RuntimeBridge) {
  return createMcpHandler(() => createPhoneMcpServer(runtime), { legacy: "reject" });
}

async function toolResult(
  operation: () => Promise<Record<string, unknown>>,
  allowImage = false,
) {
  try {
    const value = await operation();
    const image = allowImage ? imageValue(value._image) : undefined;
    const structuredContent = { ...value };
    delete structuredContent._image;
    return {
      content: [
        { type: "text" as const, text: JSON.stringify(structuredContent) },
        ...(image ? [{ type: "image" as const, data: image.data, mimeType: image.mimeType }] : []),
      ],
      structuredContent,
    };
  } catch (error) {
    const structuredContent = error instanceof RuntimeBridgeError
      ? {
          error: {
            code: error.code,
            message: error.message,
            retryable: error.detail.retryable ?? false,
            phase: error.detail.phase ?? "runtime",
            action_index: error.detail.action_index ?? null,
            completed_actions: error.detail.completed_actions ?? null,
          },
        }
      : { error: { code: "BRIDGE_UNAVAILABLE", message: "Phone runtime bridge is unavailable", retryable: false, phase: "bridge" } };
    return {
      isError: true,
      content: [{ type: "text" as const, text: JSON.stringify(structuredContent) }],
      structuredContent,
    };
  }
}

function imageValue(value: unknown): { data: string; mimeType: string } | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  const record = value as Record<string, unknown>;
  if (typeof record.data !== "string" || typeof record.mime_type !== "string") return undefined;
  if (record.mime_type !== "image/png") return undefined;
  return { data: record.data, mimeType: record.mime_type };
}
