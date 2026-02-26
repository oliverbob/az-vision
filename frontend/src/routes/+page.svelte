<script lang="ts">
  import { onMount, tick } from "svelte";

  type ChatMessage = {
    role: "user" | "assistant";
    content: string;
    createdAt: number;
    imageUrl?: string;
    attachmentName?: string;
  };

  type ConversationThread = {
    id: string;
    title: string;
    createdAt: number;
    updatedAt: number;
    messages: ChatMessage[];
  };

  let messageText = "";
  let loading = false;
  let error = "";
  let drawerOpen = false;
  let conversationScroller: HTMLDivElement | null = null;
  let composerTextarea: HTMLTextAreaElement | null = null;
  let imageInput: HTMLInputElement | null = null;
  let theme: "dark" | "light" = "dark";
  let messages: ChatMessage[] = [];
  let conversationThreads: ConversationThread[] = [];
  let activeConversationId = "";
  let selectedImageFile: File | null = null;
  let selectedImagePreview = "";
  let hasMounted = false;

  const conversationStorageKey = "zimage-conversations-v1";
  const maxPersistedMessages = 80;
  const maxPersistedConversations = 40;

  const suggestions = [
    { label: "Latest AI news", icon: "🔎", variant: "news" },
    { label: "Explain quantum computing", icon: "📚", variant: "learn" },
    { label: "Build a REST API", icon: "💻", variant: "build" },
    { label: "Career advice", icon: "🎯", variant: "career" },
  ];
  const navItems = [
    { icon: "◉", label: "Messenger" },
    { icon: "◇", label: "Courses" },
    { icon: "✦", label: "Masterclasses" },
    { icon: "▣", label: "My Files", trailingDot: true },
    { icon: "⚙", label: "Admin" },
    { icon: "☰", label: "Manage Sandboxes" },
    { icon: "〉_", label: "Console" },
  ];
  const collapsedIcons = ["✎", "⌕", "◉", "◇", "✦", "▣", "⚙", "☰", "〉_"];
  function createConversationTitle(threadMessages: ChatMessage[]): string {
    const firstUserMessage = threadMessages.find((msg) => msg.role === "user" && msg.content.trim().length > 0);
    if (!firstUserMessage) {
      return "New chat";
    }
    const text = firstUserMessage.content.trim();
    return text.length > 36 ? `${text.slice(0, 36)}...` : text;
  }

  function createNewConversation(seedMessages: ChatMessage[] = []): ConversationThread {
    const now = Date.now();
    return {
      id: crypto.randomUUID(),
      title: createConversationTitle(seedMessages),
      createdAt: now,
      updatedAt: now,
      messages: seedMessages,
    };
  }

  function persistActiveMessages(nextMessages: ChatMessage[]) {
    if (!activeConversationId) {
      return;
    }

    const sanitized = sanitizeMessagesForStorage(nextMessages);
    const now = Date.now();
    conversationThreads = conversationThreads
      .map((thread) =>
        thread.id === activeConversationId
          ? {
              ...thread,
              messages: sanitized,
              updatedAt: now,
              title: createConversationTitle(sanitized),
            }
          : thread,
      )
      .sort((a, b) => b.updatedAt - a.updatedAt)
      .slice(0, maxPersistedConversations);
  }

  function setMessages(nextMessages: ChatMessage[]) {
    messages = nextMessages;
    persistActiveMessages(nextMessages);
  }

  async function startNewChat() {
    const thread = createNewConversation();
    conversationThreads = [thread, ...conversationThreads].slice(0, maxPersistedConversations);
    activeConversationId = thread.id;
    messages = [];
    messageText = "";
    error = "";
    clearSelectedImage();
    resetComposerHeight();
    await tick();
    scrollConversationToBottom();
  }

  async function openConversation(threadId: string) {
    const thread = conversationThreads.find((item) => item.id === threadId);
    if (!thread) {
      return;
    }

    activeConversationId = threadId;
    messages = thread.messages;
    messageText = "";
    error = "";
    clearSelectedImage();
    await tick();
    scrollConversationToBottom();
  }

  function formatConversationTime(timestamp: number): string {
    const diffMinutes = Math.max(1, Math.floor((Date.now() - timestamp) / 60000));
    if (diffMinutes < 60) {
      return `${diffMinutes}m`;
    }
    const diffHours = Math.floor(diffMinutes / 60);
    if (diffHours < 24) {
      return `${diffHours}h`;
    }
    return `${Math.floor(diffHours / 24)}d`;
  }

  async function sendMessage() {
    const text = messageText.trim();
    if ((!text && !selectedImageFile) || loading) {
      return;
    }

    const history = [...messages];
    const fileToSend = selectedImageFile;
    const previewToSend = selectedImagePreview;

    const nextUserMessage = {
      role: "user",
      content: text || "Please edit this image.",
      createdAt: Date.now(),
      imageUrl: previewToSend || undefined,
      attachmentName: fileToSend?.name,
    } as const;

    setMessages([...messages, nextUserMessage]);
    await tick();
    scrollConversationToBottom();
    messageText = "";
    clearSelectedImage();
    resetComposerHeight();
    error = "";
    loading = true;

    try {
      const response = fileToSend
        ? await fetch("/api/chat", {
            method: "POST",
            body: (() => {
              const form = new FormData();
              form.set("message", text);
              form.set("history", JSON.stringify(history));
              form.set("image", fileToSend);
              return form;
            })(),
          })
        : await fetch("/api/chat", {
            method: "POST",
            headers: {
              "content-type": "application/json",
            },
            body: JSON.stringify({
              message: text,
              history,
            }),
          });

      if (!response.ok) {
        const details = await response.text();
        throw new Error(details || `Request failed: ${response.status}`);
      }

      const data = (await response.json()) as { reply: string; imageUrl?: string | null };

      setMessages([
        ...messages,
        {
          role: "assistant",
          content: data.reply,
          createdAt: Date.now(),
          imageUrl: typeof data.imageUrl === "string" ? data.imageUrl : undefined,
        },
      ]);

      await tick();
      scrollConversationToBottom();

      if (typeof data.imageUrl === "string") {
        await tick();
        scrollToLatestGeneratedImage();
      }
    } catch (e) {
      error = e instanceof Error ? e.message : "Chat failed";
    } finally {
      loading = false;
    }
  }

  function onKeydown(event: KeyboardEvent) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void sendMessage();
    }
  }

  function useSuggestion(value: string) {
    messageText = value;
    resizeComposer();
  }

  function openFilePicker() {
    imageInput?.click();
  }

  function fileToDataUrl(file: File): Promise<string> {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(typeof reader.result === "string" ? reader.result : "");
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(file);
    });
  }

  function clearSelectedImage() {
    selectedImageFile = null;
    selectedImagePreview = "";
    if (imageInput) {
      imageInput.value = "";
    }
  }

  async function onImageSelected(event: Event) {
    const target = event.currentTarget as HTMLInputElement;
    const file = target.files?.[0] ?? null;
    if (!file || !file.type.startsWith("image/")) {
      clearSelectedImage();
      return;
    }

    selectedImageFile = file;
    try {
      selectedImagePreview = await fileToDataUrl(file);
    } catch {
      clearSelectedImage();
    }
  }

  function resizeComposer() {
    if (!composerTextarea) {
      return;
    }

    composerTextarea.style.height = "auto";
    const maxHeight = 208;
    const nextHeight = Math.min(composerTextarea.scrollHeight, maxHeight);
    composerTextarea.style.height = `${nextHeight}px`;
    composerTextarea.style.overflowY = composerTextarea.scrollHeight > maxHeight ? "auto" : "hidden";
  }

  function resetComposerHeight() {
    if (!composerTextarea) {
      return;
    }

    composerTextarea.style.height = "44px";
    composerTextarea.style.overflowY = "hidden";
  }

  function toggleDrawer() {
    drawerOpen = !drawerOpen;
  }

  function applyTheme(nextTheme: "dark" | "light") {
    theme = nextTheme;
    document.documentElement.setAttribute("data-theme", nextTheme);
    localStorage.setItem("zimage-theme", nextTheme);
  }

  function toggleTheme() {
    applyTheme(theme === "dark" ? "light" : "dark");
  }

  function scrollToLatestGeneratedImage() {
    if (!conversationScroller) {
      return;
    }

    const generatedImages = conversationScroller.querySelectorAll<HTMLImageElement>('img[data-generated-image="true"]');
    const latestGeneratedImage = generatedImages[generatedImages.length - 1];

    latestGeneratedImage?.scrollIntoView({
      behavior: "smooth",
      block: "nearest",
      inline: "nearest",
    });
  }

  function scrollConversationToBottom() {
    if (!conversationScroller) {
      return;
    }

    conversationScroller.scrollTo({
      top: conversationScroller.scrollHeight,
      behavior: "smooth",
    });
  }

  function sanitizeMessagesForStorage(input: ChatMessage[]): ChatMessage[] {
    return input
      .slice(-maxPersistedMessages)
      .map((msg) => ({
        role: msg.role,
        content: msg.content,
        createdAt: msg.createdAt,
        imageUrl: msg.imageUrl,
        attachmentName: msg.attachmentName,
      }));
  }

  function persistConversations(inputThreads: ConversationThread[], inputActiveId: string) {
    const payload = {
      activeConversationId: inputActiveId,
      conversations: inputThreads.slice(0, maxPersistedConversations).map((thread) => ({
        ...thread,
        messages: sanitizeMessagesForStorage(thread.messages),
      })),
    };

    for (let count = payload.conversations.length; count >= 0; count -= 1) {
      const nextPayload = {
        ...payload,
        conversations: payload.conversations.slice(0, count),
      };
      try {
        localStorage.setItem(conversationStorageKey, JSON.stringify(nextPayload));
        return;
      } catch {
        continue;
      }
    }

    try {
      localStorage.removeItem(conversationStorageKey);
    } catch {
      // no-op
    }
  }

  function loadPersistedConversations(): { threads: ConversationThread[]; activeId: string } {
    try {
      const raw = localStorage.getItem(conversationStorageKey);
      if (!raw) {
        return { threads: [], activeId: "" };
      }

      const parsed = JSON.parse(raw) as unknown;
      if (typeof parsed !== "object" || parsed === null) {
        return { threads: [], activeId: "" };
      }

      const payload = parsed as {
        activeConversationId?: unknown;
        conversations?: unknown;
      };

      const rawConversations = Array.isArray(payload.conversations) ? payload.conversations : [];

      const threads = rawConversations
        .filter((entry): entry is Record<string, unknown> => typeof entry === "object" && entry !== null)
        .map((entry) => {
          const rawMessages = Array.isArray(entry.messages) ? entry.messages : [];
          const normalizedMessages = rawMessages
            .filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null)
            .map((item) => {
              const role: ChatMessage["role"] = item.role === "assistant" ? "assistant" : "user";
              const content = typeof item.content === "string" ? item.content : "";
              const createdAt = typeof item.createdAt === "number" ? item.createdAt : Date.now();
              const imageUrl = typeof item.imageUrl === "string" ? item.imageUrl : undefined;
              const attachmentName = typeof item.attachmentName === "string" ? item.attachmentName : undefined;
              return { role, content, createdAt, imageUrl, attachmentName };
            })
            .filter((msg) => msg.content.length > 0 || typeof msg.imageUrl === "string")
            .slice(-maxPersistedMessages);

          const createdAt = typeof entry.createdAt === "number" ? entry.createdAt : Date.now();
          const updatedAt = typeof entry.updatedAt === "number" ? entry.updatedAt : createdAt;

          return {
            id: typeof entry.id === "string" && entry.id.length > 0 ? entry.id : crypto.randomUUID(),
            title: typeof entry.title === "string" && entry.title.trim().length > 0 ? entry.title : createConversationTitle(normalizedMessages),
            createdAt,
            updatedAt,
            messages: normalizedMessages,
          } as ConversationThread;
        })
        .slice(0, maxPersistedConversations)
        .sort((a, b) => b.updatedAt - a.updatedAt);

      const activeId =
        typeof payload.activeConversationId === "string" && threads.some((thread) => thread.id === payload.activeConversationId)
          ? payload.activeConversationId
          : (threads[0]?.id ?? "");

      return { threads, activeId };
    } catch {
      return { threads: [], activeId: "" };
    }
  }

  $: if (hasMounted) {
    persistConversations(conversationThreads, activeConversationId);
  }

  onMount(() => {
    drawerOpen = window.matchMedia("(min-width: 1024px)").matches;

    const restored = loadPersistedConversations();
    if (restored.threads.length > 0) {
      conversationThreads = restored.threads;
      activeConversationId = restored.activeId;
      messages = restored.threads.find((thread) => thread.id === restored.activeId)?.messages ?? [];
    } else {
      const starter = createNewConversation();
      conversationThreads = [starter];
      activeConversationId = starter.id;
      messages = [];
    }

    hasMounted = true;
    if (messages.length > 0) {
      void tick().then(() => scrollConversationToBottom());
    }

    const saved = localStorage.getItem("zimage-theme");
    if (saved === "dark" || saved === "light") {
      applyTheme(saved);
      return;
    }

    const preferredDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    applyTheme(preferredDark ? "dark" : "light");
  });
</script>

<main class="flex min-h-dvh bg-[#040817] text-slate-200">
  {#if drawerOpen}
    <button
      type="button"
      aria-label="Close drawer"
      class="fixed inset-0 z-20 bg-black/50 lg:hidden"
      on:click={toggleDrawer}
    ></button>
  {/if}

  <aside
    class="fixed inset-y-0 left-0 z-30 w-64 bg-[#111827] transform-gpu transition-[transform,width] duration-[200ms] ease-[cubic-bezier(0.2,0.0,0.0,1.0)] {drawerOpen
      ? 'translate-x-0 lg:w-64'
      : '-translate-x-full lg:translate-x-0 lg:w-12 lg:border-r collapsed-sidebar-divider'}"
  >
    {#if !drawerOpen}
      <div class="group absolute left-[9px] top-[9px] z-40 transition-none">
        <img src="/favicon.png" alt="Ginto" class="size-[30px] rounded-full object-cover" />
        <button
          type="button"
          aria-label="Expand drawer"
          class="absolute left-1/2 top-1/2 z-20 hidden size-9 -translate-x-1/2 -translate-y-1/2 cursor-pointer place-items-center text-white opacity-55 transition-opacity duration-150 hover:opacity-100 group-hover:opacity-100 lg:grid"
          on:click={toggleDrawer}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round" class="size-6 drop-shadow-[0_1px_2px_rgba(0,0,0,0.7)]">
            <path d="m9 6 6 6-6 6" />
          </svg>
        </button>
      </div>
    {/if}

    <div class="flex h-full flex-col">
      {#if !drawerOpen}
        <div class="relative min-h-0 flex-1">
          <div class="h-12 shrink-0"></div>
          <div class="sidebar-scroll -mt-[12px] h-full overflow-y-auto overflow-x-hidden pt-0 pb-4">
            {#each collapsedIcons as icon, idx}
              <button
                type="button"
              class={`flex h-11 w-full items-center justify-start pl-[15px] text-[18px] leading-none text-slate-400 hover:bg-slate-800/50 hover:text-slate-200 ${idx === 1 ? 'text-[20px] translate-x-px' : ''} ${idx > 2 ? 'pointer-events-none opacity-0' : 'opacity-100'}`}
              >
                {icon}
              </button>
              {#if idx === collapsedIcons.length - 1}
                <div class="my-4 h-px w-7 justify-self-center bg-slate-700/50 opacity-0"></div>
              {/if}
            {/each}

            <button type="button" class="pointer-events-none flex h-11 w-full items-center justify-start pl-[15px] text-[18px] leading-none text-slate-400 opacity-0">⌁</button>
            <button type="button" class="pointer-events-none flex h-11 w-full items-center justify-start pl-[15px] text-[18px] leading-none text-slate-400 opacity-0">▣</button>
            <button type="button" class="pointer-events-none flex h-11 w-full items-center justify-start pl-[15px] text-[18px] leading-none text-slate-400 opacity-0">⚙</button>
          </div>
          <div class="pointer-events-none absolute inset-x-0 bottom-0 h-10 bg-gradient-to-t from-[#111827] to-transparent"></div>
        </div>
      {:else}
        <div
          class="min-w-0 flex min-h-0 flex-1 flex-col overflow-hidden"
        >
          <div class="sidebar-scroll min-h-0 flex-1 overflow-y-auto">
            <div class="sticky top-0 z-40 bg-[#111827]">
              <div class="grid h-12 grid-cols-[1fr_auto] items-center pl-[9px] pr-3">
                <img src="/favicon.png" alt="Ginto" class="size-[30px] rounded-full object-cover" />
                <button
                  type="button"
                  aria-label="Collapse drawer"
                  class="grid size-7 cursor-pointer place-items-center rounded-md text-[28px] leading-none text-slate-300 hover:bg-slate-800/50"
                  on:click={toggleDrawer}
                >
                  ‹
                </button>
              </div>

              <div class="-mt-[12px] px-2 pt-0 pb-3">
                <button type="button" class="sidebar-interactive grid h-11 w-full grid-cols-[48px_1fr] items-center rounded-xl pr-3 text-left text-slate-300 hover:bg-slate-700/40" on:click={startNewChat}>
                  <span class="flex h-11 items-center justify-start pl-[7px] text-[18px] leading-none text-slate-400">✎</span>
                  <span>New chat</span>
                </button>

                <div class="sidebar-subtle-field mt-0 grid h-11 w-full grid-cols-[48px_1fr] items-center rounded-lg border pr-3 text-sm text-slate-400">
                  <span class="flex h-11 -translate-y-px items-center justify-start pl-[7px] text-[20px] leading-none text-slate-400">⌕</span>
                  <span>Search chats</span>
                </div>

                {#if navItems[0]}
                  <button class="sidebar-interactive grid h-11 w-full grid-cols-[48px_1fr_auto] items-center rounded-xl pr-3 text-left text-slate-300 hover:bg-slate-700/40">
                    <span class="flex h-11 items-center justify-start pl-[7px] text-[18px] leading-none text-slate-400">{navItems[0].icon}</span>
                    <span>{navItems[0].label}</span>
                    <span class="ml-2 inline-block w-3 text-right text-emerald-400">{navItems[0].trailingDot ? '●' : ''}</span>
                  </button>
                {/if}
              </div>
            </div>

            <div class="px-2 pb-3">
            <nav class="-mt-[12px]">
              {#each navItems.slice(1) as item}
                <button class="sidebar-interactive grid h-11 w-full grid-cols-[48px_1fr_auto] items-center rounded-xl pr-3 text-left text-slate-300 hover:bg-slate-700/40">
                  <span class="flex h-11 items-center justify-start pl-[7px] text-[18px] leading-none text-slate-400">{item.icon}</span>
                  <span>{item.label}</span>
                  <span class="ml-2 inline-block w-3 text-right text-emerald-400">{item.trailingDot ? '●' : ''}</span>
                </button>
              {/each}
            </nav>

            <div class="mt-3 border-t border-slate-700/50 pt-3">
              <div class="space-y-1 pr-1">
                {#if conversationThreads.length === 0}
                  <p class="px-[7px] py-2 text-xs text-slate-500">No chats yet</p>
                {:else}
                  {#each conversationThreads as conversation}
                    <button
                      type="button"
                      class="sidebar-interactive flex w-full items-center rounded-lg py-2 pl-[7px] pr-3 text-left text-sm text-slate-300 hover:bg-slate-700/40 {conversation.id === activeConversationId ? 'bg-slate-700/40' : ''}"
                      on:click={() => openConversation(conversation.id)}
                    >
                      <span class="truncate">{conversation.title}</span>
                      <span class="history-time ml-auto text-xs">{formatConversationTime(conversation.updatedAt)}</span>
                    </button>
                  {/each}
                {/if}
              </div>
            </div>
            </div>
          </div>
        </div>
      {/if}

      {#if drawerOpen}
        <div class="mt-auto border-t border-slate-700/50 px-3 py-2.5">
          <div class="flex items-center gap-2 text-slate-300">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="size-5 text-slate-400">
              <path d="m10 13a5 5 0 0 0 7.07 0l2.12-2.12a5 5 0 0 0-7.07-7.07L10.8 5.13" />
              <path d="m14 11a5 5 0 0 0-7.07 0L4.8 13.12a5 5 0 0 0 7.07 7.07L13.2 18.87" />
            </svg>
            <span class="text-[15px]">Your Referral Link</span>
          </div>

          <div class="mt-2 flex items-center gap-2">
            <div class="sidebar-subtle-field min-w-0 flex-1 rounded-lg border px-3 py-2 text-sm text-slate-200">
              <span class="block truncate">http://ginto.ai/register?ref=a0b2d...</span>
            </div>
              <button type="button" aria-label="Copy referral link" class="sidebar-interactive grid size-9 place-items-center rounded-lg text-slate-300 hover:bg-slate-700/40">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="size-5">
                <rect x="9" y="9" width="11" height="11" rx="2" ry="2" />
                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
              </svg>
            </button>
          </div>

          <div class="mt-2 grid h-11 grid-cols-[48px_1fr_auto] items-center rounded-xl pr-2 text-[15px] text-slate-200">
            <span class="flex h-11 items-center justify-start pl-[7px]">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="size-[18px] text-slate-400">
                <circle cx="12" cy="12" r="3" />
                <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33h.09a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v.09a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z" />
              </svg>
            </span>
            <span>Settings</span>
            <span class="inline-flex items-center gap-2 text-slate-300"><span class="inline-block size-3 rounded-full bg-emerald-400"></span>Live</span>
          </div>
        </div>

        <div class="flex h-14 items-center justify-between border-t border-slate-700/50 px-3">
          <div class="inline-flex items-center gap-3 text-sm text-slate-100">
            <div class="grid size-8 place-items-center rounded-full bg-indigo-500 text-lg text-white">A</div>
            <span>admin</span>
          </div>
          <button type="button" aria-label="Sign out" class="grid size-9 place-items-center rounded-lg text-rose-300 hover:bg-rose-500/10 hover:text-rose-200">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="size-5">
              <path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4" />
              <path d="M10 17l5-5-5-5" />
              <path d="M15 12H3" />
            </svg>
          </button>
        </div>
      {:else}
        <div class="mt-auto flex h-14 items-center justify-center border-t border-slate-700/50">
          <div class="grid size-8 place-items-center rounded-full bg-indigo-500/80 text-lg text-white">A</div>
        </div>
      {/if}
    </div>
  </aside>

  <div class="flex min-w-0 flex-1 flex-col transition-[margin-left] duration-[200ms] ease-[cubic-bezier(0.2,0.0,0.0,1.0)] lg:ml-12 {drawerOpen ? 'lg:ml-64' : 'lg:ml-12'}">
    <header class="sticky top-0 z-10 flex h-14 items-center justify-between border-b border-[#1b2443] bg-[#030915]/95 px-3 backdrop-blur sm:px-5">
      <div class="flex items-center gap-2">
        <button
          type="button"
          class="grid size-9 place-items-center rounded-lg border border-[#2f3859] bg-[#0d1830] text-slate-300 lg:hidden"
          on:click={toggleDrawer}
          aria-label="Open drawer"
        >
          ☰
        </button>
        <button class="model-pill inline-flex items-center gap-2.5 rounded-[8px] border border-[#2f3859] px-3.5 py-1.5 text-sm font-medium text-slate-200">
        <span class="inline-block size-2 rounded-full bg-emerald-400"></span>
        Z-image-turbo
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" class="model-pill-caret ml-0.5 size-4 shrink-0 text-slate-400">
          <path d="m6 9 6 6 6-6" />
        </svg>
        </button>
      </div>

      <div class="flex items-center gap-2 text-slate-400">
        <button type="button" aria-label="Home" class="topbar-icon grid size-8 cursor-pointer place-items-center rounded-lg text-slate-400 hover:bg-[#0d1830] hover:text-slate-200">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="size-5">
            <path d="M3 10.5 12 3l9 7.5" />
            <path d="M5 9.8V21h5.2v-5.8h3.6V21H19V9.8" />
          </svg>
        </button>

        <button type="button" aria-label="Notifications" class="topbar-icon grid size-8 cursor-pointer place-items-center rounded-lg text-slate-400 hover:bg-[#0d1830] hover:text-slate-200">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="size-5">
            <path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9" />
            <path d="M10.3 20a2 2 0 0 0 3.4 0" />
          </svg>
        </button>

        <button type="button" aria-label="Share" class="topbar-icon grid size-8 cursor-pointer place-items-center rounded-lg text-slate-400 hover:bg-[#0d1830] hover:text-slate-200">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="size-5">
            <circle cx="18" cy="5" r="2" />
            <circle cx="6" cy="12" r="2" />
            <circle cx="18" cy="19" r="2" />
            <path d="m8 12 8-6" />
            <path d="m8 12 8 6" />
          </svg>
        </button>

        <button type="button" aria-label="Star us on GitHub" class="topbar-icon inline-flex h-8 cursor-pointer items-center gap-1.5 rounded-lg px-2.5 text-base text-slate-400 hover:bg-[#0d1830] hover:text-slate-200">
          <svg viewBox="0 0 24 24" fill="currentColor" class="size-5">
            <path d="M12 2a10 10 0 0 0-3.16 19.48c.5.1.68-.22.68-.48v-1.68c-2.78.6-3.37-1.18-3.37-1.18-.45-1.14-1.1-1.45-1.1-1.45-.9-.62.07-.6.07-.6 1 .06 1.52 1.04 1.52 1.04.88 1.5 2.31 1.06 2.87.8.09-.64.35-1.07.63-1.32-2.22-.25-4.56-1.1-4.56-4.94 0-1.1.39-2 1.03-2.72-.1-.25-.45-1.28.1-2.67 0 0 .84-.27 2.75 1.04a9.5 9.5 0 0 1 5 0c1.9-1.31 2.74-1.04 2.74-1.04.55 1.39.2 2.42.1 2.67.64.72 1.03 1.62 1.03 2.72 0 3.85-2.35 4.69-4.58 4.93.36.32.67.95.67 1.92V21c0 .26.18.59.69.48A10 10 0 0 0 12 2Z" />
          </svg>
          <span>Star us</span>
        </button>

        <button
          type="button"
          aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
          class="topbar-icon grid size-8 cursor-pointer place-items-center rounded-lg text-slate-400 hover:bg-[#0d1830] hover:text-slate-200"
          on:click={toggleTheme}
        >
          {#if theme === "dark"}
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="size-5">
              <circle cx="12" cy="12" r="4" />
              <path d="M12 2v2" />
              <path d="M12 20v2" />
              <path d="m4.93 4.93 1.41 1.41" />
              <path d="m17.66 17.66 1.41 1.41" />
              <path d="M2 12h2" />
              <path d="M20 12h2" />
              <path d="m6.34 17.66-1.41 1.41" />
              <path d="m19.07 4.93-1.41 1.41" />
            </svg>
          {:else}
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="size-5">
              <path d="M12 3a7 7 0 1 0 9 9 9 9 0 1 1-9-9Z" />
            </svg>
          {/if}
        </button>
      </div>
    </header>

    <section class="light-mode-canvas flex min-h-0 flex-1 flex-col px-3 pb-4 pt-4 sm:px-6">
      <div class="mx-auto flex w-full max-w-4xl flex-1 flex-col">
        <div class="flex-1 overflow-y-auto pb-6" bind:this={conversationScroller}>
          {#if messages.length === 0}
            <div class="mx-auto mt-8 flex max-w-2xl flex-col items-center text-center sm:mt-10">
              <div
                class="mb-4 grid size-14 place-items-center rounded-2xl bg-gradient-to-b from-violet-500 to-indigo-600 text-2xl shadow-[0_10px_30px_rgba(99,102,241,0.25)]"
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" class="size-7 text-white/95">
                  <path d="M21 11.5a8.5 8.5 0 0 1-8.5 8.5c-1.2 0-2.34-.25-3.37-.7L4 21l1.8-4.5A8.5 8.5 0 1 1 21 11.5Z" />
                  <circle cx="9" cy="11.5" r="1" fill="currentColor" stroke="none" />
                  <circle cx="12.5" cy="11.5" r="1" fill="currentColor" stroke="none" />
                  <circle cx="16" cy="11.5" r="1" fill="currentColor" stroke="none" />
                </svg>
              </div>
              <h1 class="hero-title text-3xl font-semibold text-slate-100">Ginto Chat</h1>
              <p class="hero-subtitle mt-3 max-w-xl text-lg text-slate-400">
                Type a message or upload files to get started. I can help you build, analyze, and create.
              </p>

              <div class="hero-info-card mt-5 w-full rounded-2xl border border-[#25304f] bg-[#0a1229] p-5 text-left shadow-[0_8px_30px_rgba(0,0,0,0.35)]">
                <p class="hero-card-title text-center text-xl font-semibold text-slate-100">
                  <span class="inline-flex items-center gap-2">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="hero-card-icon size-5">
                      <rect x="3" y="4" width="18" height="12" rx="2" />
                      <path d="M8 20h8" />
                      <path d="M12 16v4" />
                    </svg>
                    <span>A Non-bloated, Flexible Agentic UI</span>
                  </span>
                </p>
                <p class="hero-card-copy mt-3 text-center text-base text-slate-400">
                  Compatible with <span class="hero-highlight">Ollama</span>, <span class="hero-link">llama.cpp</span>, and OpenAI-compatible APIs.
                </p>
                <p class="hero-card-copy mt-1 text-center text-base text-slate-400">
                  Supports <span class="hero-highlight">reasoning</span>, <span class="hero-highlight">vision</span>, and <span class="hero-highlight">tool-calling</span> with multi-tenant sandboxes.
                </p>
                <p class="hero-card-footnote mt-1 text-center text-base text-slate-500">Built to scale · Built to last</p>
              </div>

              <div class="mt-5 grid w-full grid-cols-1 gap-2.5 sm:grid-cols-2">
                {#each suggestions as suggestion}
                  <button
                    type="button"
                    class="hero-suggestion rounded-xl border border-[#2a3557] bg-[#121b33] px-4 py-3 text-left text-base text-slate-300 transition hover:border-[#465b8f] hover:bg-[#172440]"
                    data-variant={suggestion.variant}
                    on:click={() => useSuggestion(suggestion.label)}
                  >
                    <span class="hero-suggestion-content">
                      <span class="hero-suggestion-icon" aria-hidden="true">{suggestion.icon}</span>
                      <span>{suggestion.label}</span>
                    </span>
                  </button>
                {/each}
              </div>
            </div>
          {:else}
            <div class="mx-auto flex w-full max-w-4xl flex-col gap-3">
              {#each messages as msg, i (msg.createdAt + i)}
                <article class="flex {msg.role === 'user' ? 'justify-end' : 'justify-start'}">
                  <div
                    class="max-w-[86%] rounded-2xl border px-4 py-3 text-[15px] leading-relaxed sm:max-w-[74%] {msg.role === 'user'
                      ? 'border-indigo-400/30 bg-indigo-600/30 text-indigo-50'
                      : 'border-[#2a3557] bg-[#111b34] text-slate-200'}"
                  >
                    <p class="whitespace-pre-wrap">{msg.content}</p>
                    {#if msg.imageUrl}
                      <img
                        src={msg.imageUrl}
                        alt="Generated result"
                        data-generated-image="true"
                        class="mt-3 w-full rounded-xl border border-[#2a3557]"
                        loading="lazy"
                      />
                    {/if}
                  </div>
                </article>
              {/each}

              {#if loading}
                <article class="flex justify-start">
                  <div class="max-w-[86%] rounded-2xl border border-[#2a3557] bg-[#111b34] px-4 py-3 text-[15px] text-slate-300 sm:max-w-[74%]">
                    Thinking...
                  </div>
                </article>
              {/if}
            </div>
          {/if}

          {#if error}
            <p class="mx-auto mt-3 max-w-4xl text-sm text-rose-300">{error}</p>
          {/if}
        </div>

        <form
          class="composer-bg sticky bottom-2 mx-auto grid w-full max-w-4xl grid-cols-[auto_1fr_auto] items-center gap-1.5 rounded-3xl border border-transparent p-2 shadow-[0_10px_35px_rgba(0,0,0,0.45)]"
          on:submit|preventDefault={sendMessage}
        >
          <button
            type="button"
            aria-label="Attach file"
            class="oa-tooltip-custom composer-action grid size-10 cursor-pointer place-items-center rounded-full text-3xl leading-none"
            on:click={openFilePicker}
          >
            +
            <span class="oa-tooltip-bubble" role="tooltip" aria-hidden="true">
              <span>Add files and more</span>
              <span class="oa-tooltip-key">/</span>
            </span>
          </button>

          <input
            bind:this={imageInput}
            type="file"
            accept="image/*"
            class="hidden"
            on:change={onImageSelected}
          />

          <div class="min-w-0">
            {#if selectedImageFile}
              <div class="mb-2 flex items-center gap-2 rounded-xl border border-[#2a3557] bg-[#111b34] px-2 py-2">
                {#if selectedImagePreview}
                  <img src={selectedImagePreview} alt="Selected attachment preview" class="size-12 rounded-md border border-[#2a3557] object-cover" />
                {/if}
                <div class="min-w-0 flex-1 text-sm text-slate-300">
                  <p class="truncate">{selectedImageFile.name}</p>
                </div>
                <button
                  type="button"
                  class="grid size-7 place-items-center rounded-md text-slate-300 hover:bg-slate-700/50"
                  aria-label="Remove selected image"
                  on:click={clearSelectedImage}
                >
                  ✕
                </button>
              </div>
            {/if}

            <textarea
              bind:this={composerTextarea}
              bind:value={messageText}
              placeholder={selectedImageFile ? "Describe your edit..." : "Ask anything..."}
              rows="1"
              on:keydown={onKeydown}
              on:input={resizeComposer}
              disabled={loading}
              class="sidebar-scroll composer-bg composer-textarea min-h-10 max-h-52 w-full resize-none rounded-2xl border border-transparent px-2 py-2 text-[1.05rem] leading-6 text-slate-100 outline-none ring-0 focus:border-transparent focus:outline-none focus:ring-0"
            ></textarea>
          </div>

          <div class="flex items-center gap-1">
            <button
              type="button"
              aria-label="Microphone"
              class="oa-tooltip composer-action grid size-9 cursor-pointer place-items-center rounded-full"
              data-tooltip="Dictate"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="size-6">
                <path d="M12 15a3 3 0 0 0 3-3V7a3 3 0 0 0-6 0v5a3 3 0 0 0 3 3Z" />
                <path d="M19 11a7 7 0 0 1-14 0" />
                <path d="M12 18v3" />
                <path d="M8 21h8" />
              </svg>
            </button>

            {#if messageText.trim() || selectedImageFile}
              <button
                type="submit"
                disabled={loading}
                aria-label="Send"
                class="oa-tooltip grid size-10 cursor-pointer place-items-center rounded-full bg-slate-100 text-slate-950 transition hover:brightness-95 disabled:cursor-not-allowed disabled:opacity-45"
                data-tooltip="Send"
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.7" stroke-linecap="round" stroke-linejoin="round" class="size-5">
                  <path d="M12 5v14" />
                  <path d="m6 11 6-6 6 6" />
                </svg>
              </button>
            {:else}
              <button
                type="button"
                aria-label="Voice"
                data-tooltip="Voice"
                class="oa-tooltip composer-voice grid size-10 cursor-pointer place-items-center rounded-full transition hover:brightness-95"
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" class="size-5">
                  <path d="M4 10v4" />
                  <path d="M8 8v8" />
                  <path d="M12 6v12" />
                  <path d="M16 8v8" />
                  <path d="M20 10v4" />
                </svg>
              </button>
            {/if}
          </div>
        </form>
      </div>
    </section>
  </div>
</main>

<style>
  .oa-tooltip {
    position: relative;
  }

  .oa-tooltip::before {
    content: attr(data-tooltip);
    position: absolute;
    left: 50%;
    bottom: calc(100% + 10px);
    transform: translateX(-50%) translateY(4px);
    border-radius: 12px;
    background: #000;
    color: #fff;
    padding: 8px 12px;
    font-size: 14px;
    line-height: 1;
    white-space: nowrap;
    opacity: 0;
    pointer-events: none;
    transition: opacity 120ms ease, transform 120ms ease;
    z-index: 60;
  }

  .oa-tooltip:hover::before,
  .oa-tooltip:focus-visible::before {
    opacity: 1;
    transform: translateX(-50%) translateY(0);
  }

  .oa-tooltip-custom {
    position: relative;
  }

  .oa-tooltip-bubble {
    position: absolute;
    left: 50%;
    bottom: calc(100% + 10px);
    transform: translateX(-50%) translateY(4px);
    border-radius: 12px;
    background: #000;
    color: #fff;
    padding: 8px 12px;
    font-size: 14px;
    line-height: 1;
    white-space: nowrap;
    display: inline-flex;
    align-items: center;
    gap: 8px;
    opacity: 0;
    pointer-events: none;
    transition: opacity 120ms ease, transform 120ms ease;
    z-index: 61;
  }

  .oa-tooltip-key {
    border-radius: 8px;
    background: #3a3a3a;
    color: #d1d5db;
    padding: 2px 7px;
    font-size: 12px;
    line-height: 1;
  }

  .oa-tooltip-custom:hover .oa-tooltip-bubble,
  .oa-tooltip-custom:focus-visible .oa-tooltip-bubble {
    opacity: 1;
    transform: translateX(-50%) translateY(0);
  }
</style>