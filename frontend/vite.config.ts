import { sveltekit } from "@sveltejs/kit/vite";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig, loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");

  const frontendPort = Number(env.FRONTEND_PORT ?? "4040");
  const backendPort = Number(env.BACKEND_PORT ?? "9090");
  const hmrHost = env.VITE_HMR_HOST?.trim();
  const hmrProtocol = env.VITE_HMR_PROTOCOL?.trim() as "ws" | "wss" | undefined;
  const hmrClientPort = env.VITE_HMR_CLIENT_PORT ? Number(env.VITE_HMR_CLIENT_PORT) : undefined;

  const backendTarget = `http://127.0.0.1:${Number.isFinite(backendPort) ? backendPort : 9090}`;

  return {
    plugins: [tailwindcss(), sveltekit()],
    server: {
      host: "0.0.0.0",
      port: Number.isFinite(frontendPort) ? frontendPort : 4040,
      strictPort: true,
      proxy: {
        "/v1": { target: backendTarget, changeOrigin: true },
        "/api/generate": { target: backendTarget, changeOrigin: true },
        "/api/tags": { target: backendTarget, changeOrigin: true },
        "/api/show": { target: backendTarget, changeOrigin: true },
      },
      hmr:
        hmrHost || hmrProtocol || Number.isFinite(hmrClientPort)
          ? {
              host: hmrHost || undefined,
              protocol: hmrProtocol || undefined,
              clientPort: Number.isFinite(hmrClientPort) ? hmrClientPort : undefined,
            }
          : undefined,
    },
  };
});