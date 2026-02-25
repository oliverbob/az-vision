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

    const configuredModelChatUrl = env.MODEL_CHAT_URL?.trim() || "http://127.0.0.1:9090/api/chat";
    let configuredPath = "/api/chat";
    try {
      const parsedConfiguredUrl = new URL(configuredModelChatUrl);
      configuredPath = parsedConfiguredUrl.pathname || "/api/chat";
    } catch {
      configuredPath = configuredModelChatUrl.includes("/v1/chat/completions")
        ? "/v1/chat/completions"
        : "/api/chat";
    }

    const modelChatPath = configuredPath.includes("/v1/chat/completions")
      ? "/v1/chat/completions"
      : "/api/chat";
    const modelChatUrl = `http://127.0.0.1:9090${modelChatPath}`;
    const modelName = env.MODEL_NAME?.trim() || "Z-image-turbo";
    const defaultHeight = Number(env.ZIMAGE_HEIGHT ?? "512");
    const defaultWidth = Number(env.ZIMAGE_WIDTH ?? "512");
    const defaultSteps = Number(env.ZIMAGE_STEPS ?? "4");
    const defaultGuidance = Number(env.ZIMAGE_GUIDANCE_SCALE ?? "0.0");

    const isOllamaApi = modelChatPath === "/api/chat";

    const upstreamPayload = isOllamaApi
      ? {
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
          options: {
            height: Number.isFinite(defaultHeight) ? defaultHeight : 512,
            width: Number.isFinite(defaultWidth) ? defaultWidth : 512,
            num_inference_steps: Number.isFinite(defaultSteps) ? defaultSteps : 4,
            guidance_scale: Number.isFinite(defaultGuidance) ? defaultGuidance : 0.0,
          },
          stream: false,
        }
      : {
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

    const imageBase64 =
      typeof data?.message?.images?.[0] === "string"
        ? data.message.images[0]
        : typeof data?.images?.[0] === "string"
          ? data.images[0]
          : null;

    const imageUrl = imageBase64
      ? imageBase64.startsWith("data:image")
        ? imageBase64
        : `data:image/png;base64,${imageBase64}`
      : null;

    return json({ reply, imageUrl });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown server error";
    return json({ error: message }, { status: 502 });
  }
};