const statusEl = document.getElementById("status");
const stopBtn = document.getElementById("stopBtn");

const headPanEl = document.getElementById("headPan");
const headTiltEl = document.getElementById("headTilt");
const waistEl = document.getElementById("waist");

const jxEl = document.getElementById("jx");
const jyEl = document.getElementById("jy");
const lEl = document.getElementById("lval");
const rEl = document.getElementById("rval");

const joyBase = document.getElementById("joyBase");
const joyStick = document.getElementById("joyStick");

let lastSent = 0;
let joyX = 0;
let joyY = 0;

const neutralTimers = new Map();

async function postJSON(url, data) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  return res.json();
}

function clamp(x, lo, hi) {
  return Math.max(lo, Math.min(hi, x));
}

// Match backend robot_control.py drive_joystick() exactly
function arcadeToWheels(x, y) {
  const angle = -45 * Math.PI / 180;
  const xRot = x * Math.cos(angle) - y * Math.sin(angle);
  const yRot = x * Math.sin(angle) + y * Math.cos(angle);

  let left = yRot + xRot;
  let right = yRot - xRot;

  const m = Math.max(1, Math.abs(left), Math.abs(right));
  left /= m;
  right /= m;

  return { left, right };
}

function updateReadout() {
  jxEl.textContent = joyX.toFixed(2);
  jyEl.textContent = joyY.toFixed(2);
  const { left, right } = arcadeToWheels(joyX, joyY);
  lEl.textContent = left.toFixed(2);
  rEl.textContent = right.toFixed(2);
}

async function sendControl(payload) {
  const now = Date.now();
  if (now - lastSent < 50) return;
  lastSent = now;

  try {
    const data = await postJSON("/api/control", payload);
    statusEl.textContent = data.ok ? "OK" : `ERR: ${data.error}`;
  } catch (e) {
    statusEl.textContent = "ERR: network";
  }
}

const baseRect = () => joyBase.getBoundingClientRect();
const baseRadius = 240 / 2;
const stickRadius = 80 / 2;

function setStickPosition(px, py) {
  const dist = Math.hypot(px, py);
  const max = baseRadius - stickRadius;
  if (dist > max) {
    const s = max / dist;
    px *= s;
    py *= s;
  }

  const center = baseRadius - stickRadius;
  joyStick.style.left = `${center + px}px`;
  joyStick.style.top = `${center + py}px`;

  joyX = clamp(px / max, -1, 1);
  joyY = clamp(-py / max, -1, 1);

  updateReadout();
  sendControl({ joy_x: joyX, joy_y: joyY });
}

function resetStick() {
  setStickPosition(0, 0);
}

let dragging = false;

function pointerPosToDelta(e) {
  const r = baseRect();
  const cx = r.left + r.width / 2;
  const cy = r.top + r.height / 2;
  return { dx: e.clientX - cx, dy: e.clientY - cy };
}

joyBase.addEventListener("pointerdown", (e) => {
  dragging = true;
  joyBase.setPointerCapture(e.pointerId);
  const { dx, dy } = pointerPosToDelta(e);
  setStickPosition(dx, dy);
});

joyBase.addEventListener("pointermove", (e) => {
  if (!dragging) return;
  const { dx, dy } = pointerPosToDelta(e);
  setStickPosition(dx, dy);
});

joyBase.addEventListener("pointerup", () => {
  dragging = false;
  resetStick();
});

joyBase.addEventListener("pointercancel", () => {
  dragging = false;
  resetStick();
});

function sendHeadWaist() {
  sendControl({
    head_pan: parseFloat(headPanEl.value),
    head_tilt: parseFloat(headTiltEl.value),
    waist: parseFloat(waistEl.value),
  });
}

headPanEl.addEventListener("input", () => {
  cancelNeutral("headPan");
  sendHeadWaist();
});

headTiltEl.addEventListener("input", () => {
  cancelNeutral("headTilt");
  sendHeadWaist();
});

waistEl.addEventListener("input", () => {
  cancelNeutral("waist");
  sendHeadWaist();
});

function cancelNeutral(id) {
  const t = neutralTimers.get(id);
  if (t) {
    clearInterval(t);
    neutralTimers.delete(id);
  }
}

function smoothToNeutral(inputEl, id, stepPerTick = 0.02, tickMs = 20) {
  cancelNeutral(id);

  const timer = setInterval(() => {
    let v = parseFloat(inputEl.value);

    if (Math.abs(v) <= stepPerTick) {
      inputEl.value = "0";
      sendHeadWaist();
      clearInterval(timer);
      neutralTimers.delete(id);
      return;
    }

    v = v > 0 ? v - stepPerTick : v + stepPerTick;
    inputEl.value = v.toFixed(2);
    sendHeadWaist();
  }, tickMs);

  neutralTimers.set(id, timer);
}

document.querySelectorAll(".neutralBtn").forEach((btn) => {
  btn.addEventListener("click", () => {
    const target = btn.getAttribute("data-target");
    if (target === "headTilt") smoothToNeutral(headTiltEl, "headTilt");
    if (target === "headPan") smoothToNeutral(headPanEl, "headPan");
    if (target === "waist") smoothToNeutral(waistEl, "waist");
  });
});

stopBtn.addEventListener("click", async () => {
  cancelNeutral("headTilt");
  cancelNeutral("headPan");
  cancelNeutral("waist");

  headPanEl.value = "0";
  headTiltEl.value = "0";
  waistEl.value = "0";
  resetStick();

  try {
    await postJSON("/api/stop", {});
    statusEl.textContent = "STOPPED";
  } catch {
    statusEl.textContent = "ERR: network";
  }
});

document.querySelectorAll(".say").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const key = btn.getAttribute("data-key");
    try {
      const data = await postJSON("/api/say", { key });
      statusEl.textContent = data.ok ? "SPOKE" : `ERR: ${data.error}`;
    } catch {
      statusEl.textContent = "ERR: network";
    }
  });
});

resetStick();
updateReadout();
