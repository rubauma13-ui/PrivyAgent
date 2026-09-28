import { extractPageElementTargets, isInteractable, PageElementTarget } from "../utils/dom";
import { ActionExecutionResult, BrowserAction, UIElement } from "../shared/types";

console.log("[PrivyAgent] Content script loaded.");

const pageElements = new Map<number, PageElementTarget>();
const MAX_SCROLL_AMOUNT = 2_000;
const MAX_TEXT_LENGTH = 10_000;
const MAX_SELECT_VALUE_LENGTH = 1_000;

function refreshPageElements(): UIElement[] {
  const targets = extractPageElementTargets();
  pageElements.clear();
  for (const target of targets) pageElements.set(target.id, target);
  return targets.map((target) => target.descriptor);
}

function getTarget(elementId: number): HTMLElement | null {
  if (!isValidId(elementId)) return null;
  const target = pageElements.get(elementId);
  if (!target || !target.element.isConnected || !isInteractable(target.element)) return null;
  const element = target.element;
  if (element.tagName.toLowerCase() !== target.descriptor.tag) return null;
  if (element instanceof HTMLInputElement && element.type !== target.descriptor.type) return null;
  return element;
}

function dispatchInputEvents(element: HTMLElement): void {
  element.dispatchEvent(new Event("input", { bubbles: true }));
  element.dispatchEvent(new Event("change", { bubbles: true }));
}

function setNativeValue(element: HTMLInputElement | HTMLTextAreaElement, value: string): void {
  const prototype = element instanceof HTMLInputElement ? HTMLInputElement.prototype : HTMLTextAreaElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
  if (setter) setter.call(element, value); else element.value = value;
}

function executeAction(candidate: unknown): ActionExecutionResult {
  const action = parseAction(candidate);
  if (!action) return { success: false, error: "The requested browser action is invalid." };
  if (action.action === "scroll") {
    window.scrollBy({ top: action.direction === "down" ? action.amount : -action.amount, behavior: "auto" });
    return { success: true };
  }
  const target = getTarget(action.element_id);
  if (!target) return { success: false, error: "The requested element is stale, unavailable, or not interactable." };
  if (action.action === "click") {
    target.click();
    return { success: true };
  }
  if (action.action === "type") return typeIntoElement(target, action.text);
  return selectOption(target, action.value);
}

function typeIntoElement(target: Element, text: string): ActionExecutionResult {
  if (target instanceof HTMLInputElement) {
    if (!isTextInput(target) || target.disabled || target.readOnly) return { success: false, error: "The requested input cannot be typed into." };
    target.focus(); setNativeValue(target, text); dispatchInputEvents(target);
    return { success: true };
  }
  if (target instanceof HTMLTextAreaElement) {
    if (target.disabled || target.readOnly) return { success: false, error: "The requested input cannot be typed into." };
    target.focus(); setNativeValue(target, text); dispatchInputEvents(target);
    return { success: true };
  }
  if (target instanceof HTMLElement && target.isContentEditable) {
    target.focus(); 
    
    // Select ALL existing content so insertText replaces it (prevents duplication)
    const selection = window.getSelection();
    if (selection) {
      selection.removeAllRanges();
      const range = document.createRange();
      range.selectNodeContents(target);
      selection.addRange(range);
    }

    // Use execCommand to simulate real typing, which triggers React's internal state properly
    if (!document.execCommand("insertText", false, text)) {
        target.textContent = text; 
    }
    dispatchInputEvents(target);
    return { success: true };
  }
  return { success: false, error: "The requested element is not a supported text input." };
}

function selectOption(target: Element, value: string): ActionExecutionResult {
  if (!(target instanceof HTMLSelectElement) || target.disabled) return { success: false, error: "The requested element is not an enabled select control." };
  const option = Array.from(target.options).find((candidate) => candidate.value === value);
  if (!option || option.disabled) return { success: false, error: "The requested select option is unavailable." };
  target.focus(); target.value = value; dispatchInputEvents(target);
  return { success: true };
}

function isTextInput(input: HTMLInputElement): boolean {
  return !["button", "checkbox", "file", "hidden", "image", "password", "radio", "range", "reset", "submit"].includes(input.type);
}

function parseAction(value: unknown): BrowserAction | null {
  if (!isRecord(value) || typeof value.action !== "string") return null;
  if (value.action === "click" && isValidId(value.element_id)) return { action: "click", element_id: value.element_id };
  if (value.action === "type" && isValidId(value.element_id) && isBoundedString(value.text, MAX_TEXT_LENGTH)) return { action: "type", element_id: value.element_id, text: value.text };
  if (value.action === "select" && isValidId(value.element_id) && isBoundedString(value.value, MAX_SELECT_VALUE_LENGTH)) return { action: "select", element_id: value.element_id, value: value.value };
  if (value.action === "scroll" && (value.direction === "up" || value.direction === "down") && isValidScrollAmount(value.amount)) return { action: "scroll", direction: value.direction, amount: value.amount };
  return null;
}

function isValidId(value: unknown): value is number { return typeof value === "number" && Number.isSafeInteger(value) && value > 0; }
function isBoundedString(value: unknown, maximumLength: number): value is string { return typeof value === "string" && value.length <= maximumLength; }
function isValidScrollAmount(value: unknown): value is number { return typeof value === "number" && Number.isSafeInteger(value) && value >= 1 && value <= MAX_SCROLL_AMOUNT; }
function isRecord(value: unknown): value is Record<string, unknown> { return typeof value === "object" && value !== null && !Array.isArray(value); }

chrome.runtime.onMessage.addListener((message: unknown, _sender, sendResponse) => {
  if (!isRecord(message)) return;
  if (message.type === "EXTRACT_PAGE") {
    const elements = refreshPageElements();
    console.log("[PrivyAgent] Detected elements:", elements);
    sendResponse({ success: true, elements });
    return;
  }
  if (message.type === "EXECUTE_ACTION") sendResponse(executeAction(message.action));
});
