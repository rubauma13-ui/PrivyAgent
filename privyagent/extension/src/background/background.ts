import {
  ActionExecutionResult, BrowserAction, PageCapture, ScreenshotCapture,
  TaskResult, TurnRecord, UIElement, VisionResponse
} from "../shared/types";

const SERVER_URL = "http://127.0.0.1:8000/vision";
const CONTENT_TIMEOUT = 10_000;
const SERVER_TIMEOUT = 600_000;
const MAX_TEXT = 10_000;
const MAX_VALUE = 1_000;
const MAX_SCROLL = 2_000;

/**
 * A task can need several actions ("open youtube and search mr beast").
 * Each turn re-extracts the page, so element IDs are always derived from the
 * DOM as it is right now and can never be executed against a stale page.
 */
const MAX_TURNS = 12;
const SETTLE_TIMEOUT = 8_000;

/**
 * Screenshots are captured but NOT sent by default.
 *
 * Nothing in the pipeline redacts pixels yet - there is no face detector and no
 * on-screen-text detector wired in - so a raw screenshot crossing the network
 * would bypass the whole privacy layer. Flip this on only once visual redaction
 * exists on this side of the boundary.
 */
const SEND_SCREENSHOT = false;

const tabQueues = new Map<number, Promise<void>>();

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type !== "SEND_TASK" || typeof message.task !== "string") return;
  handleTask(message.task).then((data) => sendResponse({ success: true, data })).catch((error: unknown) => {
    console.error("[PrivyAgent] Task failed:", error);
    sendResponse({ success: false, error: error instanceof Error ? error.message : "Unexpected background error." });
  });
  return true;
});

async function handleTask(task: string): Promise<TaskResult> {
  if (!task.trim()) throw new Error("Task cannot be empty.");
  const tab = await getActiveWebTab();
  return withTabLock(tab.id, () => runAgentLoop(task, tab));
}

async function runAgentLoop(task: string, tab: chrome.tabs.Tab & { id: number }): Promise<TaskResult> {
  const turns: TurnRecord[] = [];
  let sessionId: string | undefined;
  let privacy: TaskResult["privacy"];
  let completed = false;
  let message = "Stopped: turn limit reached.";

  for (let turn = 0; turn < MAX_TURNS; turn += 1) {
    const page = await capturePage(tab.id);
    const response = await sendToServer(task, page, sessionId);

    sessionId = response.session_id ?? sessionId;
    privacy = response.privacy ?? privacy;

    const record: TurnRecord = {
      step: response.steps?.[response.step_index ?? 0],
      status: response.status
    };

    if (response.status === "done") {
      turns.push(record);
      completed = true;
      message = "Task complete.";
      break;
    }

    if (response.status === "cannot_identify" || response.status === "error") {
      record.error = response.reason ?? "The server could not produce an action.";
      turns.push(record);
      message = record.error;
      break;
    }

    const action = parseVisionAction(response);
    if (!action) {
      record.error = "The server returned no action.";
      turns.push(record);
      message = record.error;
      break;
    }

    record.action = action;
    const execution = await sendToContentScript<ActionExecutionResult>(tab.id, { type: "EXECUTE_ACTION", action });
    record.executed = execution.success;

    if (!execution.success) {
      record.error = execution.error ?? "The browser action could not be completed.";
      turns.push(record);
      message = record.error;
      break;
    }

    turns.push(record);

    if (!response.more_steps) {
      completed = true;
      message = "Task complete.";
      break;
    }

    await waitForPageToSettle(tab.id);
  }

  await endSession(sessionId);
  return { sessionId, turns, completed, privacy, message };
}

/**
 * An action can navigate, replace the DOM, or trigger an async render. Give the
 * page a moment and wait for the tab to stop loading before re-extracting.
 */
async function waitForPageToSettle(tabId: number): Promise<void> {
  const deadline = Date.now() + SETTLE_TIMEOUT;
  await delay(400);
  while (Date.now() < deadline) {
    try {
      const tab = await chrome.tabs.get(tabId);
      if (tab.status === "complete") break;
    } catch {
      break;
    }
    await delay(200);
  }
  await delay(300);
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function withTabLock<T>(tabId: number, work: () => Promise<T>): Promise<T> {
  const previous = tabQueues.get(tabId) ?? Promise.resolve();
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  const queued = previous.catch(() => undefined).then(() => gate);
  tabQueues.set(tabId, queued);

  await previous.catch(() => undefined);
  try {
    return await work();
  } finally {
    release();
    if (tabQueues.get(tabId) === queued) tabQueues.delete(tabId);
  }
}

async function getActiveWebTab(): Promise<chrome.tabs.Tab & { id: number }> {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || typeof tab.id !== "number") throw new Error("No active tab found.");
  if (typeof tab.url !== "string" || !/^https?:\/\//i.test(tab.url)) throw new Error("PrivyAgent can only operate on regular http:// or https:// webpages.");
  return tab as chrome.tabs.Tab & { id: number };
}

async function capturePage(tabId: number): Promise<PageCapture> {
  const elements = await getPageElements(tabId);
  const screenshot = SEND_SCREENSHOT ? await captureScreenshot() : { success: false, error: "Screenshot withheld: no on-device visual redaction yet." };
  const tab = await chrome.tabs.get(tabId);
  return { elements, screenshot, url: tab.url ?? "", title: tab.title ?? "" };
}

async function getPageElements(tabId: number): Promise<UIElement[]> {
  try { return await requestElements(tabId); }
  catch (error) {
    if (!(error instanceof Error) || !error.message.includes("Receiving end does not exist")) throw error;
    await chrome.scripting.executeScript({ target: { tabId }, files: ["content/content.js"] });
    return requestElements(tabId);
  }
}

async function requestElements(tabId: number): Promise<UIElement[]> {
  const response = await sendToContentScript<{ success: boolean; elements: UIElement[] }>(tabId, { type: "EXTRACT_PAGE" });
  if (!response.success || !Array.isArray(response.elements)) throw new Error("The page returned an invalid element list.");
  return response.elements;
}

async function captureScreenshot(): Promise<ScreenshotCapture> {
  try {
    const dataUrl = await chrome.tabs.captureVisibleTab({ format: "png" });
    return dataUrl ? { success: true, dataUrl } : { success: false, error: "Chrome returned an empty screenshot." };
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : "Screenshot capture failed.";
    console.warn("[PrivyAgent] Screenshot capture failed:", message);
    return { success: false, error: message };
  }
}

async function sendToServer(task: string, page: PageCapture, sessionId?: string): Promise<VisionResponse> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), SERVER_TIMEOUT);
  try {
    const response = await fetch(SERVER_URL, {
      method: "POST",
      signal: controller.signal,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task, page, session_id: sessionId })
    });
    if (!response.ok) throw new Error(`PrivyAgent server returned HTTP ${response.status}.`);
    const result: unknown = await response.json();
    if (!isRecord(result)) throw new Error("PrivyAgent server returned invalid JSON.");
    return result as VisionResponse;
  } catch (error: unknown) {
    if (error instanceof DOMException && error.name === "AbortError") throw new Error("PrivyAgent server request timed out.");
    if (error instanceof TypeError) throw new Error("Could not reach the PrivyAgent server on 127.0.0.1:8000. Is it running?");
    throw error;
  } finally { clearTimeout(timeout); }
}

/** Best effort: drop the server-side session so its PII vault is released. */
async function endSession(sessionId?: string): Promise<void> {
  if (!sessionId) return;
  try {
    await fetch("http://127.0.0.1:8000/session/end", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId })
    });
  } catch {
    // The session expires on its own; a failure here is not worth surfacing.
  }
}

function parseVisionAction(response: VisionResponse): BrowserAction | undefined {
  if (!("action" in response)) { console.info("[PrivyAgent] Server response contains no browser action."); return; }
  const action = response.action;
  if (!isRecord(action) || typeof action.action !== "string") throw new Error("The server returned a malformed browser action.");
  if (action.action === "click" && validId(action.element_id)) return { action: "click", element_id: action.element_id };
  if (action.action === "type" && validId(action.element_id) && typeof action.text === "string" && action.text.length <= MAX_TEXT) return { action: "type", element_id: action.element_id, text: action.text };
  if (action.action === "select" && validId(action.element_id) && typeof action.value === "string" && action.value.length <= MAX_VALUE) return { action: "select", element_id: action.element_id, value: action.value };
  if (action.action === "scroll" && (action.direction === "up" || action.direction === "down") && validAmount(action.amount)) return { action: "scroll", direction: action.direction, amount: action.amount };
  throw new Error("The server returned an unsupported or malformed browser action.");
}

function validId(value: unknown): value is number { return typeof value === "number" && Number.isSafeInteger(value) && value > 0; }
function validAmount(value: unknown): value is number { return typeof value === "number" && Number.isSafeInteger(value) && value >= 1 && value <= MAX_SCROLL; }
function isRecord(value: unknown): value is Record<string, unknown> { return typeof value === "object" && value !== null && !Array.isArray(value); }

function sendToContentScript<T>(tabId: number, message: object): Promise<T> {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error("The page did not respond before the timeout.")), CONTENT_TIMEOUT);
    chrome.tabs.sendMessage(tabId, message, (response: T) => {
      clearTimeout(timeout);
      if (chrome.runtime.lastError) { reject(new Error(chrome.runtime.lastError.message)); return; }
      if (response === undefined) { reject(new Error("No response received from the page.")); return; }
      resolve(response);
    });
  });
}
