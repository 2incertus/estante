// Screenshots of the dev InkyPi (scripts/dev-inkypi.sh run) at desktop and phone sizes.
//
//   NODE_PATH=/home/ubuntu/node_modules node scripts/shoot-inkypi.js [path ...]
//
// Default paths: /, /plugin/the_shelf, /plugin/now_listening. Writes
// out/shots/<name>-desktop.png and <name>-mobile.png and prints any console errors
// plus the values of the shelf settings form, so prepopulation can be checked.
const path = require("path");
const fs = require("fs");
const { chromium } = require("playwright");

const BASE = process.env.INKYPI_URL || "http://127.0.0.1:18080";
const OUT = path.join(__dirname, "..", "out", "shots");
const VIEWPORTS = { desktop: { width: 1280, height: 800 }, mobile: { width: 390, height: 844 } };
const DEFAULT_PATHS = ["/", "/plugin/the_shelf", "/plugin/now_listening"];

function shotName(p) {
  const url = new URL(p, BASE);
  let name = url.pathname.replace(/^\/+|\/+$/g, "").replace(/\//g, "-") || "home";
  const instance = url.searchParams.get("instance");
  if (instance) name += "-" + instance.toLowerCase().replace(/[^a-z0-9]+/g, "_");
  return name;
}

(async () => {
  const paths = process.argv.length > 2 ? process.argv.slice(2) : DEFAULT_PATHS;
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  let failed = false;
  try {
    for (const [label, viewport] of Object.entries(VIEWPORTS)) {
      const page = await browser.newPage({ viewport });
      page.on("console", (msg) => {
        if (msg.type() === "error") console.log(`  console error (${label}): ${msg.text()}`);
      });
      for (const p of paths) {
        const resp = await page.goto(BASE + p, { waitUntil: "networkidle" });
        const file = path.join(OUT, `${shotName(p)}-${label}.png`);
        await page.screenshot({ path: file, fullPage: true });
        console.log(`${resp.status()} ${p} -> ${file}`);
        if (resp.status() !== 200) failed = true;
        const form = await page.$("#estanteLibrary");
        if (form) {
          const values = await page.$$eval("#settingsForm select, #settingsForm input", (els) =>
            els.filter((el) => el.name).map((el) => `${el.name}=${el.value}`)
          );
          console.log(`  form: ${values.join("  ")}`);
        }
      }
      await page.close();
    }
  } finally {
    await browser.close();
  }
  process.exit(failed ? 1 : 0);
})();
