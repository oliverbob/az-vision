import { json, type RequestHandler } from "@sveltejs/kit";
import { env } from "$env/dynamic/private";

type IncomingChatMessage = {
  role: "user" | "assistant";
  content: string;
  createdAt?: number;
};

export const POST: RequestHandler = async ({ request, fetch }) => {
  try {
    const body = (await request.json()) as {
      message?: string;
      history?: IncomingChatMessage[];
    };

    const message = body.message?.trim() ?? "";
    const history = Array.isArray(body.history) ? body.history : [];

    if (!message) {
      return json({ error: "Message is required" }, { status: 400 });
    }

    const modelChatUrl = "http://127.0.0.1:9090/v1/chat/completions";
    const modelName = env.MODEL_NAME?.trim() || "Z-image-turbo";
    const defaultHeight = Number(env.ZIMAGE_HEIGHT ?? "512");
    const defaultWidth = Number(env.ZIMAGE_WIDTH ?? "512");
    const defaultSteps = Number(env.ZIMAGE_STEPS ?? "4");
    const defaultGuidance = Number(env.ZIMAGE_GUIDANCE_SCALE ?? "0.0");

    const upstreamPayload = {
      model: modelName,
      messages: [
        ...history.map((entry) => ({
          role: entry.role,
          content: entry.content,
        })),
        {
          role: "user",
          content: message,
        },
      ],
      stream: false,
      height: Number.isFinite(defaultHeight) ? defaultHeight : 512,
      width: Number.isFinite(defaultWidth) ? defaultWidth : 512,
      num_inference_steps: Number.isFinite(defaultSteps) ? defaultSteps : 4,
      guidance_scale: Number.isFinite(defaultGuidance) ? defaultGuidance : 0.0,
    };

    let upstream: Response;
    try {
      upstream = await fetch(modelChatUrl, {
        method: "POST",
        headers: {
          "content-type": "application/json",
        },
        body: JSON.stringify(upstreamPayload),
      });
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : String(error);
      return json(
        {
          error: "Model backend is unreachable",
          target: modelChatUrl,
          details: errorMessage,
        },
        { status: 502 },
      );
    }

    if (!upstream.ok) {
      const errorText = await upstream.text();
      return json(
        {
          error: errorText || `Upstream error: ${upstream.status}`,
          target: modelChatUrl,
        },
        { status: 502 },
      );
    }

    const data = await upstream.json();
    const reply =
      typeof data?.reply === "string"
        ? data.reply
        : typeof data?.choices?.[0]?.message?.content === "string"
          ? data.choices[0].message.content
        : typeof data?.message?.content === "string"
          ? data.message.content
          : typeof data?.response === "string"
            ? data.response
            : JSON.stringify(data);

    const imageUrl = null;

    return json({ reply, imageUrl });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown server error";
    return json({ error: message }, { status: 502 });
  }
};