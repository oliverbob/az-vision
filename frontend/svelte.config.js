import { vitePreprocess } from "@sveltejs/vite-plugin-svelte";

let adapterFactory;
try {
  adapterFactory = (await import("@sveltejs/adapter-auto")).default;
} catch {
  adapterFactory = () => ({
    name: "noop-adapter",
    adapt: async () => {},
  });
}

const config = {
  preprocess: vitePreprocess(),
  kit: {
    adapter: adapterFactory(),
  },
};

export default config;