// Drives the web UI in headless Chrome and saves screenshots for the documents and the video.
//
//   node scripts/capture_screens.mjs OUT_DIR MAIN_URL SAMPLE_URL HELDOUT_URL
//
// It uses the Chrome DevTools protocol over the WebSocket client built into Node,
// so it needs no packages. MAIN_URL is a replay server on a fresh ledger. SAMPLE_URL
// replays robustness sample 01 and HELDOUT_URL replays held-out fixture h4, each
// from its own recording directory. Every request is typed or chosen exactly as a
// person would, and every screen is a real response from the running app.

import { spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [out, MAIN, SAMPLE, HELDOUT] = process.argv.slice(2);
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9333;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
mkdirSync(out, { recursive: true });

const profile = mkdtempSync(join(tmpdir(), "lg-capture-"));
const chrome = spawn(CHROME, [
  "--headless=new",
  `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${profile}`,
  "--hide-scrollbars",
  "--no-first-run",
  "--window-size=1440,900",
  "about:blank",
]);

async function target() {
  for (let i = 0; i < 50; i++) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
      const page = list.find((t) => t.type === "page");
      if (page) return page.webSocketDebuggerUrl;
    } catch {
      /* Chrome is still starting */
    }
    await sleep(200);
  }
  throw new Error("Chrome did not start");
}

const ws = new WebSocket(await target());
await new Promise((r) => ws.addEventListener("open", r, { once: true }));
let nextId = 1;
const waiting = new Map();
ws.addEventListener("message", (e) => {
  const msg = JSON.parse(e.data);
  if (msg.id && waiting.has(msg.id)) {
    waiting.get(msg.id)(msg);
    waiting.delete(msg.id);
  }
});
function send(method, params = {}) {
  const id = nextId++;
  ws.send(JSON.stringify({ id, method, params }));
  return new Promise((resolve, reject) =>
    waiting.set(id, (m) => (m.error ? reject(new Error(`${method}: ${m.error.message}`)) : resolve(m.result))),
  );
}
async function js(expression) {
  const r = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.text + " " + expression.slice(0, 80));
  return r.result.value;
}
async function waitFor(expression, what, timeout = 30000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    if (await js(expression)) return;
    await sleep(250);
  }
  throw new Error(`timed out waiting for ${what}`);
}
async function goto(url) {
  await send("Page.navigate", { url });
  await sleep(500);
  await waitFor("document.readyState === 'complete'", "page load");
  await sleep(800);
}
async function shot(name, selector) {
  let clip;
  if (selector) {
    const box = await js(`(() => { const el = ${selector}; if (!el) return null;
      el.scrollIntoView({block: "start"}); const r = el.getBoundingClientRect();
      return {x: r.x + scrollX, y: r.y + scrollY, width: r.width, height: Math.min(r.height, 2400)}; })()`);
    await sleep(400);
    if (box) clip = { ...box, scale: 1 };
  }
  const r = await send("Page.captureScreenshot", {
    format: "png",
    captureBeyondViewport: Boolean(clip),
    ...(clip ? { clip } : {}),
  });
  writeFileSync(join(out, `${name}.png`), Buffer.from(r.data, "base64"));
  console.log("saved", name);
}

const lastArticle = "[...document.querySelectorAll('article')].at(-1)";
const turnDone = `(() => { const a = ${lastArticle}; if (!a) return false;
  const t = a.innerText; return !t.includes("working") && /(verified|blocked|rejected|No recording)/.test(t); })()`;

async function chooseRecorded(key) {
  await js(`(() => { const s = document.querySelector("select");
    const set = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value").set;
    set.call(s, ${JSON.stringify(key)}); s.dispatchEvent(new Event("change", {bubbles: true})); })()`);
  await sleep(600);
  await waitFor(turnDone, `turn ${key}`);
  await sleep(1200);
}
async function typeRequest(text) {
  await js(`(() => { const i = document.querySelector("form input");
    const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    set.call(i, ${JSON.stringify(text)}); i.dispatchEvent(new Event("input", {bubbles: true})); })()`);
  await sleep(200);
  await js(`document.querySelector("form button[type=submit]").click()`);
  await sleep(600);
  await waitFor(turnDone, `turn "${text}"`);
  await sleep(1200);
}
async function clickButton(label) {
  await js(`(() => { const b = [...document.querySelectorAll("button")].find((x) => x.innerText.trim() === ${JSON.stringify(label)});
    b.scrollIntoView({block: "center"}); b.click(); })()`);
}
async function consoleShot(name) {
  await js(`${lastArticle}.scrollIntoView({block: "start"}); window.scrollBy(0, -16)`);
  await sleep(500);
  await shot(name);
}

await send("Page.enable");
await send("Runtime.enable");
await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 2, mobile: false });

// ---- the demo, in the order a judge follows it
await goto(`${MAIN}/#console`);
await waitFor("document.body.textContent.includes('Read-only scenarios')", "catalogue");
await shot("00_console_catalogue");

await chooseRecorded("s01_cash_quarter");
await consoleShot("01_cash_quarter_shortfall_first");
await chooseRecorded("s02_week7_why");
await consoleShot("02_week7_omission_put_back");
await chooseRecorded("s09_pay_all_suppliers");
await consoleShot("03_payroll_protection_block");
await shot("04_trust_receipt_rules", "document.querySelector('aside section')");
await chooseRecorded("s11_open_supplier_invoices");
await consoleShot("05_injection_contained");
await chooseRecorded("a3_accountant_memo");
await consoleShot("06_attached_memo_contained");
await chooseRecorded("s06_accept_factoring");
await consoleShot("07_accept_financing_t3_cost_stated");
await chooseRecorded("s07_record_brennan_payment");
await consoleShot("08_record_receipt_t2");

await goto(`${MAIN}/#inbox`);
await waitFor("document.body.textContent.includes('What approving changes')", "inbox diff");
await sleep(1500);
await shot("09_inbox_diff");
await js(`[...document.querySelectorAll("section")].find((s) => s.textContent.includes("ACCEPT_FINANCING_OFFER")).scrollIntoView({block: "start"})`);
await sleep(500);
await shot("10_inbox_t3_confirmation");

await goto(`${MAIN}/#radar`);
await waitFor("document.body.textContent.includes('13-week projection')", "radar");
await sleep(1500);
await shot("11_radar_projection");
await clickButton("Run");
await waitFor("document.body.textContent.includes('paying 30 days later')", "scenario result");
await sleep(1500);
await shot("12_radar_scenario", "[...document.querySelectorAll('section')].find((s) => s.textContent.includes('What-if scenario'))");
await shot("13_radar_financing", "[...document.querySelectorAll('section')].find((s) => s.textContent.includes('Financing options'))");

await goto(`${MAIN}/#audit`);
await waitFor("document.body.textContent.includes('Chain intact')", "audit chain");
await sleep(800);
await shot("14_audit_chain_intact");

await goto(`${MAIN}/#console`);
await waitFor("document.body.textContent.includes('Read-only scenarios')", "catalogue");
await chooseRecorded("s12_audit_trail");
await consoleShot("15_audit_trail_answer");

// ---- real model errors the verifier caught, each replayed from its own recording
if (SAMPLE) {
  await goto(`${SAMPLE}/#console`);
  await waitFor("document.body.textContent.includes('Read-only scenarios')", "catalogue");
  await typeRequest("Pay all outstanding supplier invoices this week");
  await consoleShot("16_wrong_total_masked");
}
if (HELDOUT) {
  await goto(`${HELDOUT}/#console`);
  await waitFor("document.body.textContent.includes('Read-only scenarios')", "catalogue");
  await typeRequest("What do we owe and what are we owed?");
  await js(`[...document.querySelectorAll("li")].find((l) => l.innerText.includes("model said"))?.scrollIntoView({block: "center"})`);
  await sleep(600);
  await shot("17_figure_corrected");
}

ws.close();
chrome.kill();
process.exit(0);
