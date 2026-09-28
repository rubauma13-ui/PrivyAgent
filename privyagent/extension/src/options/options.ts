const requestMicButton = document.getElementById("requestMicButton") as HTMLButtonElement;
const statusText = document.getElementById("status") as HTMLParagraphElement;

requestMicButton.addEventListener("click", async () => {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    // If we reach here, permission was granted
    stream.getTracks().forEach((track) => track.stop()); // Stop the stream immediately
    
    statusText.textContent = "Microphone permission granted! You can now use the STT feature in the extension popup.";
    statusText.className = "success";
    requestMicButton.disabled = true;
  } catch (error) {
    console.error("Microphone permission denied:", error);
    statusText.textContent = "Permission denied. Please allow microphone access in your browser settings.";
    statusText.className = "error";
  }
});

