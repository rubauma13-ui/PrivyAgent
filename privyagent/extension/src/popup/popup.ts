import { TaskResult } from "../shared/types";

const taskInput = document.getElementById("task") as HTMLTextAreaElement;
const sendButton = document.getElementById("sendButton") as HTMLButtonElement;
const sttButton = document.getElementById("sttButton") as HTMLButtonElement;
const statusText = document.getElementById("status") as HTMLParagraphElement;
const transcriptList = document.getElementById("transcript") as HTMLUListElement;
const privacyText = document.getElementById("privacy") as HTMLParagraphElement;

function setStatus(text: string, kind?: "error" | "success") {
  statusText.textContent = text;
  statusText.classList.remove("status-error", "status-success");
  if (kind) statusText.classList.add(`status-${kind}`);
}

function clearResult() {
  transcriptList.replaceChildren();
  privacyText.textContent = "";
}

/**
 * Show what actually happened, turn by turn. A single "Success!" hides the
 * useful part of a multi-step run - which step failed and why.
 */
function renderResult(result: TaskResult) {
  clearResult();

  for (const turn of result.turns) {
    const item = document.createElement("li");
    const action = turn.action ? turn.action.action : turn.status ?? "?";
    const outcome = turn.error ? ` - ${turn.error}` : turn.executed ? " - done" : "";
    item.textContent = `${turn.step ?? "step"}: ${action}${outcome}`;
    if (turn.error) item.classList.add("status-error");
    transcriptList.append(item);
  }

  const redacted = result.privacy?.redacted_values ?? 0;
  const labels = Object.entries(result.privacy?.by_label ?? {})
    .map(([label, count]) => `${label}x${count}`)
    .join(", ");
  const nerNote = result.privacy?.ner_available === false ? " (NER model not loaded)" : "";
  privacyText.textContent = redacted
    ? `Redacted ${redacted} value(s) before sending: ${labels}${nerNote}`
    : `No sensitive values detected on this page${nerNote}`;
}

sendButton.addEventListener("click", async () => {
  const task = taskInput.value.trim();

  if (!task) {
    setStatus("Enter a task.", "error");
    return;
  }

  sendButton.disabled = true;
  clearResult();
  setStatus("Running...");

  try {
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    const activeUrl = tabs[0]?.url ?? "";

    if (/^(chrome|edge|about|view-source):/.test(activeUrl)) {
      setStatus("Open a regular webpage (http:// or https://) before sending a task.", "error");
      return;
    }

    const response = await chrome.runtime.sendMessage({ type: "SEND_TASK", task });

    // chrome.runtime.sendMessage resolves to `undefined` if no listener called
    // sendResponse (the service worker errored, or was not running).
    if (response === undefined) {
      setStatus("No response from background script. Check the service worker console (chrome://extensions).", "error");
      console.error("[PrivyAgent popup] chrome.runtime.lastError:", chrome.runtime.lastError);
      return;
    }

    if (!response.success) {
      setStatus(`Failed: ${response.error ?? "Unknown error"}`, "error");
      return;
    }

    const result = response.data as TaskResult;
    renderResult(result);
    setStatus(result.message, result.completed ? "success" : "error");
  } catch (error) {
    console.error("[PrivyAgent popup] Extension error:", error);
    setStatus(`Error: ${error instanceof Error ? error.message : String(error)}`, "error");
  } finally {
    sendButton.disabled = false;
  }
});

// @ts-ignore - SpeechRecognition is not in standard lib dom yet
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

if (SpeechRecognition) {
  const recognition = new SpeechRecognition();
  recognition.continuous = false;
  recognition.interimResults = true;
  
  let isRecording = false;

  recognition.onstart = () => {
    isRecording = true;
    sttButton.textContent = "⏹️";
    sttButton.title = "Stop Dictation";
    setStatus("Listening...");
  };

  recognition.onresult = (event: any) => {
    let finalTranscript = "";
    let interimTranscript = "";

    for (let i = event.resultIndex; i < event.results.length; ++i) {
      if (event.results[i].isFinal) {
        finalTranscript += event.results[i][0].transcript;
      } else {
        interimTranscript += event.results[i][0].transcript;
      }
    }
    
    if (finalTranscript) {
      const currentVal = taskInput.value;
      taskInput.value = currentVal ? currentVal + " " + finalTranscript : finalTranscript;
    }
    // We could show interim results somewhere, but for a simple task input, just waiting for final is usually fine,
    // or we could update the textarea with interim. Let's just put it in the textarea directly if we want a fluid experience.
    // Actually, to make it simple and less buggy, let's just use final results or append.
  };

  recognition.onerror = (event: any) => {
    console.error("[PrivyAgent popup] Speech recognition error", event.error);
    if (event.error === "not-allowed") {
      setStatus("Mic access denied. Opening options page to grant permission...", "error");
      setTimeout(() => {
        chrome.runtime.openOptionsPage();
      }, 1500);
    } else {
      setStatus(`Microphone error: ${event.error}`, "error");
    }
    isRecording = false;
    sttButton.textContent = "🎤";
    sttButton.title = "Start Dictation";
  };

  recognition.onend = () => {
    isRecording = false;
    sttButton.textContent = "🎤";
    sttButton.title = "Start Dictation";
    if (statusText.textContent === "Listening...") {
      setStatus("");
    }
  };

  sttButton.addEventListener("click", () => {
    if (isRecording) {
      recognition.stop();
    } else {
      try {
        recognition.start();
      } catch (err) {
        console.error(err);
      }
    }
  });
} else {
  sttButton.style.display = "none";
}
