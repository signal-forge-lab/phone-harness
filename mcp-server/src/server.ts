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
  z.object({
    op: z.literal("ask_operator"),
    question: z.string().min(1).describe("Human-facing question. Write this in Japanese. Literal product names, technical identifiers, and physical labels may remain in English."),
    choices: z.array(z.string().min(1).describe("Human-facing choice. Write explanatory wording in Japanese; literal labels may remain in English.")).max(8).optional(),
    context: z.string().describe("Human-facing context. Write explanatory prose in Japanese; literal technical labels may remain in English.").optional(),
    confidence: z.number().min(0).max(1).optional(),
    impact: z.enum(["low", "medium", "high"]).optional(),
    timeout: z.number().positive().max(3600).optional(),
  }),
  z.object({
    op: z.literal("run_workflow"),
    name: z.enum(["merge_boss_once", "merge_boss_turn"]),
    max_producer_taps: z.number().int().min(0).max(8).optional(),
    max_relaxed_checks: z.number().int().min(0).max(8).optional(),
    max_cycles: z.number().int().min(0).max(32).optional(),
    max_merges: z.number().int().min(0).max(64).optional(),
    max_emissions: z.number().int().min(0).max(40).optional(),
    uncertain_burst_size: z.number().int().min(0).max(20).optional(),
    max_recoveries: z.number().int().min(0).max(10).optional(),
    order_rescan_every: z.number().int().min(0).max(16).optional(),
  }),
]);

const decisionContextSchema = z.object({
  summary: z.string().min(1),
  confidence: z.number().min(0).max(1).optional(),
  uncertainty: z.string().optional(),
  evidence: z.array(z.string()).max(12).optional(),
  alternatives: z.array(z.string()).max(8).optional(),
  expected_effect: z.string().optional(),
  goal: z.string().optional(),
});

const regionSchema = z.object({
  x: z.number().nonnegative(),
  y: z.number().nonnegative(),
  w: z.number().positive(),
  h: z.number().positive(),
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
    "phone_host_activity",
    {
      description: "Mark explicit host/AI work state for the local monitor. Use working when beginning or resuming a phone-harness task, completed immediately before the final user-facing completion reply, and paused when intentionally stopping before completion.",
      inputSchema: {
        state: z.enum(["working", "completed", "paused"]),
        note: z.string().max(500).optional(),
      },
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    },
    async ({ state, note }) => toolResult(() => runtime.call("host_activity", {
      state,
      ...(note === undefined ? {} : { note }),
    })),
  );

  server.registerTool(
    "phone_teaching",
    {
      description: "Read or handle operator-initiated Human Teaching messages submitted from the local monitor. The oldest pending message may include one attached image. When replying to the human operator, write explanatory prose in Japanese; literal product names and technical labels may remain in English.",
      inputSchema: {
        action: z.enum(["next", "handle"]),
        message_id: z.string().min(1).optional(),
        reply: z.string().max(16000).describe("Human-facing AI reply. Write explanatory prose in Japanese; literal product names and technical labels may remain in English.").optional(),
        learned: z.boolean().optional(),
      },
      annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: false },
    },
    async ({ action, message_id, reply, learned }) => toolResult(async () => {
      if (action === "next") return runtime.call("teaching_next", {});
      if (!message_id) throw new Error("phone_teaching handle requires message_id");
      return runtime.call("teaching_handle", {
        message_id,
        ...(reply === undefined ? {} : { reply }),
        ...(learned === undefined ? {} : { learned }),
      });
    }, action === "next"),
  );

  server.registerTool(
    "phone_observe",
    {
      description: "Observe the current iPhone screen. Uses accessibility first with OCR augmentation/fallback. An optional absolute-pixel region limits semantic/OCR/image work while returned coordinates remain full-screen coordinates. Returns temporary element_ref values for precise follow-up actions and hides document/editable text bodies by default.",
      inputSchema: {
        force: z.boolean().optional().describe("Force a fresh observation instead of using the short-lived runtime cache."),
        include_image: z.boolean().optional().describe("Also return the current screenshot as image content. Defaults to false."),
        include_text_content: z.boolean().optional().describe("Include full document/editable text content. Keep false unless the task specifically requires reading it."),
        region: regionSchema.optional().describe("Optional absolute screen-pixel rectangle {x,y,w,h}. OCR and returned image are limited to this rectangle; returned element coordinates remain absolute screen coordinates."),
        mode: z.enum(["semantic", "visual"]).optional().describe("semantic (default) uses accessibility/OCR. visual skips text analysis and returns an image, useful for icon/layout/grid decisions."),
        image_profile: z.enum(["full", "glance", "coarse", "balanced", "detail"]).optional().describe("Image quality when an image is returned. full=PNG; visual profiles are progressively larger JPEGs from 256 to 960 px long edge."),
        reuse_observation_id: z.number().int().positive().optional().describe("For visual mode, reuse the retained frame from this current observation_id so a later detail/full refinement does not capture the phone again."),
      },
      annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
    },
    async ({ force, include_image, include_text_content, region, mode, image_profile, reuse_observation_id }) => toolResult(
      () => runtime.call("observe", {
        force: force ?? false,
        include_image: include_image ?? false,
        include_text_content: include_text_content ?? false,
        ...(region === undefined ? {} : { region }),
        ...(mode === undefined ? {} : { mode }),
        ...(image_profile === undefined ? {} : { image_profile }),
        ...(reuse_observation_id === undefined ? {} : { reuse_observation_id }),
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
        decision_context: decisionContextSchema.optional().describe("Optional concise host decision summary for the local monitor. This is explicit observability metadata, not hidden chain-of-thought."),
        actions: z.array(actionSchema).min(1).max(100),
      },
      annotations: { readOnlyHint: false, destructiveHint: true, idempotentHint: false, openWorldHint: true },
    },
    async ({ observation_id, decision_context, actions }) => toolResult(async () => {
      if (decision_context !== undefined) {
        await runtime.call("decision", decision_context);
      }
      const operatorQuestion = actions.find((action) => action.op === "ask_operator");
      if (operatorQuestion !== undefined) {
        if (actions.length !== 1) throw new Error("ask_operator must be the only action in a phone_act request");
        return runtime.call("operator_question", {
          question: operatorQuestion.question,
          ...(operatorQuestion.choices === undefined ? {} : { choices: operatorQuestion.choices }),
          ...(operatorQuestion.context === undefined ? {} : { context: operatorQuestion.context }),
          ...(operatorQuestion.confidence === undefined ? {} : { confidence: operatorQuestion.confidence }),
          ...(operatorQuestion.impact === undefined ? {} : { impact: operatorQuestion.impact }),
          ...(operatorQuestion.timeout === undefined ? {} : { timeout: operatorQuestion.timeout }),
        });
      }
      const workflow = actions.find((action) => action.op === "run_workflow");
      if (workflow !== undefined) {
        if (actions.length !== 1) throw new Error("run_workflow must be the only action in a phone_act request");
        return runtime.call("workflow", {
          name: workflow.name,
          options: {
            ...(workflow.max_producer_taps === undefined ? {} : { max_producer_taps: workflow.max_producer_taps }),
            ...(workflow.max_relaxed_checks === undefined ? {} : { max_relaxed_checks: workflow.max_relaxed_checks }),
            ...(workflow.max_cycles === undefined ? {} : { max_cycles: workflow.max_cycles }),
            ...(workflow.max_merges === undefined ? {} : { max_merges: workflow.max_merges }),
            ...(workflow.max_emissions === undefined ? {} : { max_emissions: workflow.max_emissions }),
            ...(workflow.uncertain_burst_size === undefined ? {} : { uncertain_burst_size: workflow.uncertain_burst_size }),
            ...(workflow.max_recoveries === undefined ? {} : { max_recoveries: workflow.max_recoveries }),
            ...(workflow.order_rescan_every === undefined ? {} : { order_rescan_every: workflow.order_rescan_every }),
          },
        });
      }
      return runtime.call("act", {
        ...(observation_id === undefined ? {} : { observation_id }),
        actions,
      });
    }),
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
  if (!["image/png", "image/jpeg", "image/webp"].includes(record.mime_type)) return undefined;
  return { data: record.data, mimeType: record.mime_type };
}
