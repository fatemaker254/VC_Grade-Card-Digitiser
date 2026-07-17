const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("file-input");
const fileChosen = document.getElementById("file-chosen");
const startBtn = document.getElementById("start-btn");
const form = document.getElementById("upload-form");
const singlePassToggle = document.getElementById("single-pass-toggle");

const uploadSection = document.getElementById("upload-section");
const progressSection = document.getElementById("progress-section");
const resultsSection = document.getElementById("results-section");
const errorSection = document.getElementById("error-section");

const progressPercent = document.getElementById("progress-percent");
const progressBarFill = document.getElementById("progress-bar-fill");
const progressTitle = document.getElementById("progress-title");
const logConsole = document.getElementById("log-console");

const resultsSummary = document.getElementById("results-summary");
const resultsList = document.getElementById("results-list");
const errorMessage = document.getElementById("error-message");

let chosenFile = null;
let pollTimer = null;
let currentJobId = null;

function setFile(file) {
  if (!file || file.type !== "application/pdf") {
    fileChosen.textContent = "Please choose a PDF file.";
    startBtn.disabled = true;
    return;
  }
  chosenFile = file;
  fileChosen.textContent = `${file.name} (${(file.size / 1024 / 1024).toFixed(1)} MB)`;
  dropzone.classList.add("has-file");
  startBtn.disabled = false;
}

["dragenter", "dragover"].forEach(evt =>
  dropzone.addEventListener(evt, e => { e.preventDefault(); dropzone.classList.add("drag-over"); })
);
["dragleave", "drop"].forEach(evt =>
  dropzone.addEventListener(evt, e => { e.preventDefault(); dropzone.classList.remove("drag-over"); })
);
dropzone.addEventListener("drop", e => {
  const file = e.dataTransfer.files[0];
  setFile(file);
});
dropzone.addEventListener("click", e => {
  if (e.target.closest(".browse-btn")) return; // label already opens the picker
  fileInput.click();
});
fileInput.addEventListener("change", () => setFile(fileInput.files[0]));

function showSection(section) {
  [uploadSection, progressSection, resultsSection, errorSection].forEach(s => s.hidden = true);
  section.hidden = false;
}

form.addEventListener("submit", async e => {
  e.preventDefault();
  if (!chosenFile) return;

  showSection(progressSection);
  progressTitle.textContent = "Uploading\u2026";
  progressPercent.textContent = "0%";
  progressBarFill.style.width = "0%";
  logConsole.textContent = "";

  const body = new FormData();
  body.append("pdf", chosenFile);
  body.append("single_pass", singlePassToggle.checked ? "true" : "false");

  let res;
  try {
    res = await fetch("/process", { method: "POST", body });
  } catch (err) {
    return showError("Could not reach the server. Is app.py still running?");
  }
  const data = await res.json();
  if (!res.ok) return showError(data.error || "Upload failed.");

  progressTitle.textContent = "Reading pages\u2026";
  currentJobId = data.job_id;
  pollTimer = setInterval(() => pollStatus(data.job_id), 1200);
});

async function pollStatus(jobId) {
  let res, data;
  try {
    res = await fetch(`/status/${jobId}`);
    data = await res.json();
  } catch (err) {
    return; // transient network hiccup, try again next tick
  }
  if (!res.ok) return;

  progressPercent.textContent = `${data.percent}%`;
  progressBarFill.style.width = `${data.percent}%`;
  logConsole.textContent = data.log.join("\n");
  logConsole.scrollTop = logConsole.scrollHeight;

  if (data.status === "done") {
    clearInterval(pollTimer);
    showResults(data);
  } else if (data.status === "needs_review") {
    clearInterval(pollTimer);
    showNeedsReview(data);
  } else if (data.status === "error") {
    clearInterval(pollTimer);
    showError(data.error || "The pipeline hit an unexpected error.");
  }
}

function showNeedsReview(data) {
  progressTitle.textContent = "Ready for review";
  progressBarFill.style.width = "100%";
  progressPercent.textContent = "100%";

  let banner = document.getElementById("review-banner");
  if (!banner) {
    banner = document.createElement("div");
    banner.id = "review-banner";
    banner.className = "review-banner";
    progressSection.appendChild(banner);
  }
  banner.innerHTML = `
    <p><strong>${data.pending_review}</strong> field(s) across ${data.total_pages} page(s) need a quick check
    before the Excel files are written.</p>
    <a href="/review/${currentJobId}" class="primary-btn" style="width:auto; text-decoration:none; display:inline-block;">
      Review now
    </a>`;
}

function showResults(data) {
  showSection(resultsSection);
  const failedNote = data.failed ? `, ${data.failed} page(s) failed - see the log above` : "";
  resultsSummary.textContent = `${data.total_pages} page(s) processed${failedNote}.`;
  resultsList.innerHTML = "";
  (data.files || []).forEach(f => {
    const li = document.createElement("li");
    li.textContent = f;
    resultsList.appendChild(li);
  });
}

function showError(message) {
  showSection(errorSection);
  errorMessage.textContent = message;
}

document.getElementById("open-folder-btn").addEventListener("click", () => {
  fetch("/open-output", { method: "POST" });
});

document.getElementById("process-another-btn").addEventListener("click", resetForm);
document.getElementById("error-retry-btn").addEventListener("click", resetForm);

function resetForm() {
  chosenFile = null;
  currentJobId = null;
  fileInput.value = "";
  fileChosen.textContent = "";
  dropzone.classList.remove("has-file");
  startBtn.disabled = true;
  const banner = document.getElementById("review-banner");
  if (banner) banner.remove();
  showSection(uploadSection);
}
