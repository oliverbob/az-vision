import { json, type RequestHandler } from "@sveltejs/kit";
import { env } from "$env/dynamic/private";

type IncomingChatMessage = {
  role: "user" | "assistant";
  content: string;
  createdAt?: number;
};

const isDev = process.env.NODE_ENV !== "production";

function debugUpstream(message: string) {
  if (!isDev) {
    return;
  }
  console.info(`[frontend/api/chat] ${message}`);
}

function normalizeApiBase(url: URL): URL {
  const path = url.pathname.replace(/\/+$/, "");
  const endpointSuffixes = ["/api/chat", "/v1/chat/completions", "/v1/images/edits"];

  for (const suffix of endpointSuffixes) {
    if (path === suffix) {
      return new URL("/", url.origin);
    }
    if (path.endsWith(suffix)) {
      const basePath = path.slice(0, -suffix.length);
      return new URL(basePath.length > 0 ? `${basePath}/` : "/", url.origin);
    }
  }

  return new URL(path.length > 0 ? `${path}/` : "/", url.origin);
}

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

    const configuredModelChatUrl = env.MODEL_CHAT_URL?.trim() || "http://127.0.0.1:9090/";

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

    const normalizedRawUrl = configuredModelChatUrl;

    let modelChatUrl: string;
    let modelImageEditUrl: string;
    try {
      const requestOrigin = new URL(request.url).origin;
      const configuredUrl = new URL(normalizedRawUrl, requestOrigin);
      const apiBase = normalizeApiBase(configuredUrl);

      modelChatUrl = new URL("v1/chat/completions", apiBase).toString();
      modelImageEditUrl = new URL("v1/images/edits", apiBase).toString();
    } catch {
      return gracefulBackendReply("Invalid MODEL_CHAT_URL", configuredModelChatUrl);
    }

    const modelName = env.MODEL_NAME?.trim() || "Z-image-turbo";
    const defaultHeight = Number(env.ZIMAGE_HEIGHT ?? "512");
    const defaultWidth = Number(env.ZIMAGE_WIDTH ?? "512");
    const defaultSteps = Number(env.ZIMAGE_STEPS ?? "4");
    const defaultGuidance = Number(env.ZIMAGE_GUIDANCE_SCALE ?? "0.0");
    const defaultEditStrength = Number(env.ZIMAGE_EDIT_STRENGTH ?? "0.6");

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
        imageEditPayload.set("num_inference_steps", String(Number.isFinite(defaultSteps) ? defaultSteps : 4));
        imageEditPayload.set("guidance_scale", String(Number.isFinite(defaultGuidance) ? defaultGuidance : 0.0));
        imageEditPayload.set("strength", String(Number.isFinite(defaultEditStrength) ? defaultEditStrength : 0.6));
        imageEditPayload.set("response_format", "b64_json");
        imageEditPayload.set("image", attachedImage, attachedImage.name || "image.png");

        debugUpstream(`using image edit endpoint: ${modelImageEditUrl}`);
        upstream = await fetch(modelImageEditUrl, {
          method: "POST",
          body: imageEditPayload,
        });
      } else {
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

        debugUpstream(`using chat endpoint: ${modelChatUrl}`);
        upstream = await fetch(modelChatUrl, {
          method: "POST",
          headers: {
            "content-type": "application/json",
          },
          body: JSON.stringify(upstreamPayload),
        });
      }

      debugUpstream(`upstream response status: ${upstream.status}`);
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
