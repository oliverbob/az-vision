import { json, type RequestHandler } from "@sveltejs/kit";
import { env } from "$env/dynamic/private";

type IncomingChatMessage = {
  role: "user" | "assistant";
  content: string;
  createdAt?: number;
};

export const POST: RequestHandler = async ({ request, fetch }) => {
  try {
    const contentType = request.headers.get("content-type")?.toLowerCase() ?? "";
    const isMultipart = contentType.includes("multipart/form-data");

    let message = "";
    let history: IncomingChatMessage[] = [];
    let attachedImage: File | null = null;

    if (isMultipart) {
      const form = await request.formData();
      message = String(form.get("message") ?? "").trim();

      const rawHistory = form.get("history");
      if (typeof rawHistory === "string") {
        try {
          const parsed = JSON.parse(rawHistory) as IncomingChatMessage[];
          history = Array.isArray(parsed) ? parsed : [];
        } catch {
          history = [];
        }
      }

      const maybeImage = form.get("image");
      if (maybeImage instanceof File && maybeImage.size > 0) {
        attachedImage = maybeImage;
      }
    } else {
      const body = (await request.json()) as {
        message?: string;
        history?: IncomingChatMessage[];
      };

      message = body.message?.trim() ?? "";
      history = Array.isArray(body.history) ? body.history : [];
    }

    if (!message && !attachedImage) {
      return json({ error: "Message or image is required" }, { status: 400 });
    }

    const configuredModelChatUrl = env.MODEL_CHAT_URL?.trim();

    const toTargetPath = (target?: string) => {
      if (!target) {
        return null;
      }

      try {
        const parsed = new URL(target);
        return parsed.pathname || "/";
      } catch {
        return target.startsWith("/") ? target : null;
      }
    };

    const gracefulBackendReply = (reason?: string, target?: string) => {
      const suffix = reason ? ` (${reason})` : "";
      const targetPath = toTargetPath(target);
      const targetHint = targetPath ? `\nTarget: ${targetPath}` : "";
      return json({
        reply:
          `Backend is temporarily unavailable.${suffix}\nPlease ensure your model API server is reachable and try again.${targetHint}`,
        imageUrl: null,
      });
    };

    if (!configuredModelChatUrl) {
      if (attachedImage) {
        return gracefulBackendReply("MODEL_CHAT_URL is not configured");
      }
      return json({ reply: `Echo: ${message}` });
    }

    const normalizedRawUrl = /^(https?:)?\/\//i.test(configuredModelChatUrl)
      ? configuredModelChatUrl
      : `http://${configuredModelChatUrl}`;

    let modelChatUrl: string;
    let modelImageEditUrl: string;
    try {
      const configuredUrl = new URL(normalizedRawUrl);
      const upstreamPath = configuredUrl.pathname.includes("/api/chat")
        ? "/api/chat"
        : "/v1/chat/completions";
      const protocol = configuredUrl.protocol || "http:";
      const host = configuredUrl.hostname;
      if (!host) {
        return gracefulBackendReply("MODEL_CHAT_URL must include a valid host", configuredModelChatUrl);
      }
      const isLoopbackHost = host === "localhost" || host === "127.0.0.1";
      const hasExplicitPort = configuredUrl.port.length > 0;
      const baseUrl = hasExplicitPort
        ? `${protocol}//${host}:${configuredUrl.port}`
        : isLoopbackHost
          ? `${protocol}//${host}:9090`
          : `${protocol}//${host}`;
      modelChatUrl = `${baseUrl}${upstreamPath}`;
      modelImageEditUrl = `${baseUrl}/v1/images/edits`;
    } catch {
      return gracefulBackendReply("Invalid MODEL_CHAT_URL", configuredModelChatUrl);
    }
    const modelName = env.MODEL_NAME?.trim() || "Z-image-turbo";
    const defaultHeight = Number(env.ZIMAGE_HEIGHT ?? "512");
    const defaultWidth = Number(env.ZIMAGE_WIDTH ?? "512");
    const defaultSteps = Number(env.ZIMAGE_STEPS ?? "4");
    const defaultGuidance = Number(env.ZIMAGE_GUIDANCE_SCALE ?? "0.0");

    let upstream: Response;
    try {
      if (attachedImage) {
        const imageEditPayload = new FormData();
        imageEditPayload.set("model", modelName);
        imageEditPayload.set("prompt", message || "Please edit this image.");
        imageEditPayload.set("n", "1");
        imageEditPayload.set(
          "size",
          `${Number.isFinite(defaultWidth) ? defaultWidth : 512}x${Number.isFinite(defaultHeight) ? defaultHeight : 512}`,
        );
        imageEditPayload.set("response_format", "b64_json");
        imageEditPayload.set("image", attachedImage, attachedImage.name || "image.png");

        upstream = await fetch(modelImageEditUrl, {
          method: "POST",
          body: imageEditPayload,
        });
      } else {
        const isOllamaApi = modelChatUrl.includes("/api/chat");

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

        upstream = await fetch(modelChatUrl, {
          method: "POST",
          headers: {
            "content-type": "application/json",
          },
          body: JSON.stringify(upstreamPayload),
        });
      }
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : String(error);
      return gracefulBackendReply(errorMessage, attachedImage ? modelImageEditUrl : modelChatUrl);
    }

    if (!upstream.ok) {
      let upstreamMessage = `Upstream error: ${upstream.status}`;
      try {
        const errorJson = await upstream.json();
        if (typeof errorJson?.detail === "string") {
          upstreamMessage = errorJson.detail;
        } else if (typeof errorJson?.error?.message === "string") {
          upstreamMessage = errorJson.error.message;
        } else {
          upstreamMessage = JSON.stringify(errorJson);
        }
      } catch {
        const errorText = await upstream.text();
        if (errorText.trim()) {
          upstreamMessage = errorText;
        }
      }

      return gracefulBackendReply(upstreamMessage, attachedImage ? modelImageEditUrl : modelChatUrl);
    }

    const data = await upstream.json();

    const openaiContent = data?.choices?.[0]?.message?.content;
    const openaiTextFromBlocks = Array.isArray(openaiContent)
      ? openaiContent
          .filter((item: unknown) => {
            return typeof item === "object" && item !== null && (item as { type?: unknown }).type === "text";
          })
          .map((item: { text?: unknown }) => (typeof item.text === "string" ? item.text : ""))
          .filter((part: string) => part.length > 0)
          .join("\n")
      : null;

    const openaiImageUrlFromBlocks = Array.isArray(openaiContent)
      ? openaiContent.find((item: unknown) => {
          if (typeof item !== "object" || item === null) {
            return false;
          }
          const imageUrl = (item as { image_url?: { url?: unknown } }).image_url;
          return typeof imageUrl?.url === "string";
        })
      : null;

    const reply =
      typeof data?.reply === "string"
        ? data.reply
        : typeof data?.choices?.[0]?.message?.content === "string"
          ? data.choices[0].message.content
          : typeof openaiTextFromBlocks === "string" && openaiTextFromBlocks.length > 0
            ? openaiTextFromBlocks
          : typeof data?.message?.content === "string"
            ? data.message.content
            : typeof data?.response === "string"
              ? data.response
              : attachedImage
                ? "Image generation completed."
                : JSON.stringify(data);

    const imageBase64 =
      typeof data?.message?.images?.[0] === "string"
        ? data.message.images[0]
        : typeof data?.images?.[0] === "string"
          ? data.images[0]
          : typeof data?.data?.[0]?.b64_json === "string"
            ? data.data[0].b64_json
          : null;

    const imageUrlFromOpenAIBlocks =
      openaiImageUrlFromBlocks && typeof (openaiImageUrlFromBlocks as { image_url?: { url?: unknown } }).image_url?.url === "string"
        ? ((openaiImageUrlFromBlocks as { image_url: { url: string } }).image_url.url as string)
        : null;

    const imageUrl = imageBase64
      ? imageBase64.startsWith("data:image")
        ? imageBase64
        : `data:image/png;base64,${imageBase64}`
      : typeof imageUrlFromOpenAIBlocks === "string"
        ? imageUrlFromOpenAIBlocks
      : typeof data?.data?.[0]?.url === "string"
        ? data.data[0].url
      : null;

    return json({ reply, imageUrl });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown server error";
    return json({ error: message }, { status: 502 });
  }
};