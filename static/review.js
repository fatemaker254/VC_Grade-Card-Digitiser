const jobId = document.currentScript.dataset.jobId;

const reviewCounter = document.getElementById("review-counter");
const reviewEmpty = document.getElementById("review-empty");
const reviewCard = document.getElementById("review-card");
const reviewImage = document.getElementById("review-image");
const studentName = document.getElementById("review-student-name");
const regNo = document.getElementById("review-reg-no");
const courseCode = document.getElementById("review-course-code");
const label = document.getElementById("review-label");
const reason = document.getElementById("review-reason");
const suggestions = document.getElementById("review-suggestions");
const input = document.getElementById("review-input");
const confirmBtn = document.getElementById("confirm-btn");
const finishBtn = document.getElementById("finish-btn");

let queue = [];
let total = 0;

async function loadItems() {
  const res = await fetch(`/review/${jobId}/items`);
  const data = await res.json();
  queue = data.items;
  total = data.total;
  render();
}

function render() {
  const remaining = queue.length;
  const done = total - remaining;
  reviewCounter.textContent = total ? `${done} of ${total} checked` : "No items to review";

  if (remaining === 0) {
    reviewCard.hidden = true;
    reviewEmpty.hidden = false;
    return;
  }

  reviewEmpty.hidden = true;
  reviewCard.hidden = false;

  const item = queue[0];
  reviewImage.src = item.image_url;
  studentName.textContent = item.student_name || "(name not read)";
  regNo.textContent = item.registration_no || "";
  courseCode.textContent = item.course_code;
  label.textContent = item.label;
  reason.textContent = item.reason;

  suggestions.innerHTML = "";
  const seen = new Set();
  [["Pass 1", item.pass1], ["Pass 2", item.pass2]].forEach(([tag, val]) => {
    if (val === undefined || val === null || val === "" || seen.has(String(val))) return;
    seen.add(String(val));
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "suggestion-chip";
    chip.textContent = `${tag}: ${val}`;
    chip.addEventListener("click", () => { input.value = val; input.focus(); });
    suggestions.appendChild(chip);
  });

  input.value = item.current_value ?? "";
  input.focus();
  input.select();
}

confirmBtn.addEventListener("click", submitCurrent);
input.addEventListener("keydown", e => { if (e.key === "Enter") submitCurrent(); });

async function submitCurrent() {
  if (queue.length === 0) return;
  const item = queue[0];
  confirmBtn.disabled = true;
  try {
    await fetch(`/review/${jobId}/submit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: item.id, value: input.value }),
    });
    queue.shift();
    render();
  } finally {
    confirmBtn.disabled = false;
  }
}

finishBtn.addEventListener("click", async () => {
  finishBtn.disabled = true;
  finishBtn.textContent = "Writing Excel files\u2026";
  const res = await fetch(`/review/${jobId}/finish`, { method: "POST" });
  if (res.ok) {
    window.location.href = "/";
  } else {
    finishBtn.disabled = false;
    finishBtn.textContent = "Finish & write Excel files";
    alert("Could not finish yet - some items may still be unresolved.");
  }
});

loadItems();
