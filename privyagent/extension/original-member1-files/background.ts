import { ActionExecutionResult, BrowserAction, PageCapture, ScreenshotCapture, UIElement, VisionResponse } from "../shared/types";

const VISION_URL = "http://127.0.0.1:8000/vision";
const CONTENT_TIMEOUT = 10_000;
const VISION_TIMEOUT = 15_000;
const MAX_TEXT = 10_000;
const MAX_VALUE = 1_000;
const MAX_SCROLL = 2_000;
const tabQueues = new Map<number, Promise<void>>();

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type !== "SEND_TASK" || typeof message.task !== "string") return;
  handleTask(message.task).then((data) => sendResponse({ success: true, data })).catch((error: unknown) => {
    console.error("[PrivyAgent] Task failed:", error);
    sendResponse({ success: false, error: error instanceof Error ? error.message : "Unexpected background error." });
  });
  return true;
});

async function handleTask(task: string): Promise<{ vision: VisionResponse; screenshot: ScreenshotCapture; action?: ActionExecutionResult }> {
  if (!task.trim()) throw new Error("Task cannot be empty.");
  const tab = await getActiveWebTab();
  return withTabLock(tab.id, () => handleTaskForTab(task, tab));
}

async function handleTaskForTab(task: string, tab: chrome.tabs.Tab & { id: number }): Promise<{ vision: VisionResponse; screenshot: ScreenshotCapture; action?: ActionExecutionResult }> {
  const [elements, screenshot] = await Promise.all([getPageElements(tab.id), captureScreenshot()]);
  const page: PageCapture = { elements, screenshot };
  const vision = await sendTaskToMember2(task, page);
  const action = parseVisionAction(vision);
  if (!action) return { vision, screenshot };
  const execution = await sendToContentScript<ActionExecutionResult>(tab.id, { type: "EXECUTE_ACTION", action });
  if (!execution.success) throw new Error(execution.error ?? "The browser action could not be completed.");
  return { vision, screenshot, action: execution };
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

async function sendTaskToMember2(task: string, page: PageCapture): Promise<VisionResponse> {
  const controller = new AbortController(); const timeout = setTimeout(() => controller.abort(), VISION_TIMEOUT);
  try {
    const response = await fetch(VISION_URL, { method: "POST", signal: controller.signal, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ task, page }) });
    if (!response.ok) throw new Error(`Member 2 returned HTTP ${response.status}.`);
    const result: unknown = await response.json();
    if (!isRecord(result)) throw new Error("Member 2 returned invalid JSON.");
    return result;
  } catch (error: unknown) {
    if (error instanceof DOMException && error.name === "AbortError") throw new Error("Member 2 request timed out.");
    throw error;
  } finally { clearTimeout(timeout); }
}

function parseVisionAction(response: VisionResponse): BrowserAction | undefined {
  if (!("action" in response)) { console.info("[PrivyAgent] Member 2 response contains no browser action."); return; }
  const action = response.action;
  if (!isRecord(action) || typeof action.action !== "string") throw new Error("Member 2 returned a malformed browser action.");
  if (action.action === "click" && validId(action.element_id)) return { action: "click", element_id: action.element_id };
  if (action.action === "type" && validId(action.element_id) && typeof action.text === "string" && action.text.length <= MAX_TEXT) return { action: "type", element_id: action.element_id, text: action.text };
  if (action.action === "select" && validId(action.element_id) && typeof action.value === "string" && action.value.length <= MAX_VALUE) return { action: "select", element_id: action.element_id, value: action.value };
  if (action.action === "scroll" && (action.direction === "up" || action.direction === "down") && validAmount(action.amount)) return { action: "scroll", direction: action.direction, amount: action.amount };
  throw new Error("Member 2 returned an unsupported or malformed browser action.");
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
