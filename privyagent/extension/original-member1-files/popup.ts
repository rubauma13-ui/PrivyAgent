const taskInput =
  document.getElementById("task") as HTMLTextAreaElement;

const sendButton =
  document.getElementById("sendButton") as HTMLButtonElement;

const statusText =
  document.getElementById("status") as HTMLParagraphElement;


function setStatus(text: string, kind?: "error" | "success") {
  statusText.textContent = text;
  statusText.classList.remove("status-error", "status-success");
  if (kind) {
    statusText.classList.add(`status-${kind}`);
  }
}


sendButton.addEventListener("click", async () => {

  const task = taskInput.value.trim();

  if (!task) {
    setStatus("Enter a task.", "error");
    return;
  }

  sendButton.disabled = true;
  setStatus("Sending...");

  try {
    const tabs = await chrome.tabs.query({
      active: true,
      currentWindow: true
    });
    const activeUrl = tabs[0]?.url ?? "";

    if (/^(chrome|edge|about|view-source):/.test(activeUrl)) {
      setStatus(
        "Open a regular webpage (http:// or https://) before sending a task.",
        "error"
      );
      return;
    }

    const response = await chrome.runtime.sendMessage({
      type: "SEND_TASK",
      task: task
    });

    console.log(
      "[PrivyAgent popup] Response:",
      JSON.stringify(response, null, 2)
    );

    // chrome.runtime.sendMessage resolves to `undefined` if no listener
    // called sendResponse (e.g. background script errored before it
    // could respond, or the service worker wasn't running). Surface
    // that case explicitly instead of falling through silently.
    if (response === undefined) {

      setStatus(
        "No response from background script. Check the service worker console (chrome://extensions).",
        "error"
      );

      console.error(
        "[PrivyAgent popup] chrome.runtime.lastError:",
        chrome.runtime.lastError
      );

    } else if (response.success) {

      setStatus("Success! Check the extension console.", "success");

    } else {

      setStatus(`Failed: ${response.error ?? "Unknown error"}`, "error");

      console.error(
        "[PrivyAgent popup] Server error:",
        response.error
      );
    }

  } catch (error) {

    console.error(
      "[PrivyAgent popup] Extension error:",
      error
    );

    setStatus(
      `Error: ${error instanceof Error ? error.message : String(error)}`,
      "error"
    );

  } finally {
    sendButton.disabled = false;
  }

});
