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

    const modelChatUrl = env.MODEL_CHAT_URL;

    if (!modelChatUrl) {
      return json({
        reply: `Echo: ${message}\n\nSet MODEL_CHAT_URL in frontend/.env to connect a real model backend.`,
      });
    }

    const upstream = await fetch(modelChatUrl, {
      method: "POST",
      headers: {
        "content-type": "application/json",
      },
      body: JSON.stringify({
        message,
        history,
      }),
    });

    if (!upstream.ok) {
      const errorText = await upstream.text();
      return json(
        {
          error: errorText || `Upstream error: ${upstream.status}`,
        },
        { status: 502 },
      );
    }

    const data = await upstream.json();
    const reply = typeof data?.reply === "string" ? data.reply : JSON.stringify(data);

    return json({ reply });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown server error";
    return json({ error: message }, { status: 500 });
  }
};