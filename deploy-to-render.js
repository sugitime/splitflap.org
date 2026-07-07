#!/usr/bin/env node
/**
 * Deploy splitflap.org to Render.com (Node.js version)
 * Run: node deploy-to-render.js
 */
const { execSync, spawnSync } = require("child_process");
const https = require("https");
const fs = require("fs");
const path = require("path");

const RENDER_API_KEY = process.env.RENDER_API_KEY;
if (!RENDER_API_KEY) {
  console.error("Set RENDER_API_KEY before running this script.");
  process.exit(1);
}
const RENDER_OWNER_ID = "tea-d8m251i8qa3s73b06upg";
const SERVICE_NAME = "splitflap";
const GITHUB_REPO_URL = "https://github.com/sugitime/splitflap.org";
const BRANCH = "main";
const PROJECT_DIR = __dirname;
const LOG_FILE = path.join(PROJECT_DIR, "deploy-output.log");

const log = (...args) => {
  const line = args.map((a) => (typeof a === "string" ? a : JSON.stringify(a, null, 2))).join(" ");
  console.log(line);
  fs.appendFileSync(LOG_FILE, line + "\n");
};

function run(cmd, opts = {}) {
  log(`$ ${cmd}`);
  try {
    const out = execSync(cmd, {
      cwd: PROJECT_DIR,
      encoding: "utf8",
      stdio: ["pipe", "pipe", "pipe"],
      ...opts,
    });
    if (out) log(out.trimEnd());
    return out;
  } catch (e) {
    const msg = (e.stdout || "") + (e.stderr || "") + (e.message || "");
    log(msg.trimEnd());
    if (!opts.allowFail) throw e;
    return msg;
  }
}

function api(method, urlPath, body) {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null;
    const req = https.request(
      {
        hostname: "api.render.com",
        path: urlPath,
        method,
        headers: {
          Authorization: `Bearer ${RENDER_API_KEY}`,
          Accept: "application/json",
          ...(data ? { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(data) } : {}),
        },
      },
      (res) => {
        let raw = "";
        res.on("data", (c) => (raw += c));
        res.on("end", () => {
          log(`HTTP ${res.statusCode} ${method} ${urlPath}`);
          if (raw) log(raw);
          try {
            resolve({ status: res.statusCode, data: raw ? JSON.parse(raw) : null });
          } catch {
            resolve({ status: res.statusCode, data: raw });
          }
        });
      }
    );
    req.on("error", reject);
    if (data) req.write(data);
    req.end();
  });
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function main() {
  fs.writeFileSync(LOG_FILE, `=== Deploy started ${new Date().toISOString()} ===\n`);

  log("\n=== 1. Git status ===");
  run("git status");
  run("git log --oneline -3", { allowFail: true });
  run("git remote -v");

  log("\n=== 2. List Render services ===");
  const servicesRes = await api("GET", `/v1/services?ownerId=${RENDER_OWNER_ID}&limit=50`);
  let existing = null;
  if (Array.isArray(servicesRes.data)) {
    for (const item of servicesRes.data) {
      const svc = item.service;
      if (svc?.name === SERVICE_NAME) {
        existing = svc;
        log(`Found existing service: ${svc.id} -> ${svc.serviceDetails?.url}`);
      }
    }
  }
  if (!existing) log("No existing splitflap service found.");

  log("\n=== 3. GitHub repo setup ===");
  const gh = spawnSync("gh", ["auth", "status"], { encoding: "utf8", cwd: PROJECT_DIR });
  log(gh.stdout || gh.stderr || "");
  const repoView = spawnSync("gh", ["repo", "view", "sugitime/splitflap.org"], { encoding: "utf8", cwd: PROJECT_DIR });
  if (repoView.status !== 0) {
    log("Creating GitHub repo...");
    run('gh repo create sugitime/splitflap.org --public --source . --remote fork --push=false --description "Split-flap display board (Docker + Render deploy)"', { allowFail: true });
  } else {
    log("Repo sugitime/splitflap.org exists.");
  }

  const remote = run("git remote get-url origin", { allowFail: true });
  if (!remote.includes("sugitime/splitflap")) {
    run(`git remote set-url origin ${GITHUB_REPO_URL}`, { allowFail: true });
  }

  log("\n=== 4. Commit local changes ===");
  const files = ["Dockerfile", "render.yaml", ".dockerignore", "docker-compose.yml", "server.js", "package.json"];
  for (const f of files) {
    if (fs.existsSync(path.join(PROJECT_DIR, f))) run(`git add ${f}`);
  }
  const status = run("git status --porcelain", { allowFail: true });
  if (status.trim()) {
    run('git commit -m "Add Docker and Render deployment configuration"');
  } else {
    log("No uncommitted changes.");
  }

  log("\n=== 5. Push to GitHub ===");
  run(`git push -u origin ${BRANCH}`);

  log("\n=== 6. Create Render web service ===");
  let serviceId = existing?.id;
  let deployId = null;
  let serviceUrl = existing?.serviceDetails?.url;
  let dashboardUrl = existing?.dashboardUrl;

  if (!serviceId) {
    const createBody = {
      type: "web_service",
      name: SERVICE_NAME,
      ownerId: RENDER_OWNER_ID,
      repo: GITHUB_REPO_URL,
      branch: BRANCH,
      autoDeploy: "yes",
      envVars: [{ key: "NODE_ENV", value: "production" }],
      serviceDetails: {
        env: "docker",
        plan: "free",
        region: "oregon",
        healthCheckPath: "/api/health",
        envSpecificDetails: { dockerContext: ".", dockerfilePath: "./Dockerfile" },
      },
    };
    const created = await api("POST", "/v1/services", createBody);
    if (created.status === 201) {
      serviceId = created.data.service?.id;
      deployId = created.data.deployId;
      serviceUrl = created.data.service?.serviceDetails?.url;
      dashboardUrl = created.data.service?.dashboardUrl;
    } else if (created.status === 409) {
      log("Service already exists, re-fetching...");
    }
  }

  if (!serviceId) {
    const again = await api("GET", `/v1/services?ownerId=${RENDER_OWNER_ID}&limit=50`);
    for (const item of again.data || []) {
      if (item.service?.name === SERVICE_NAME) {
        serviceId = item.service.id;
        serviceUrl = item.service.serviceDetails?.url;
        dashboardUrl = item.service.dashboardUrl;
      }
    }
  }

  log("\n=== 7. Trigger deploy ===");
  if (serviceId && !deployId) {
    const dep = await api("POST", `/v1/services/${serviceId}/deploys`, {});
    if (dep.status === 201 || dep.status === 200) deployId = dep.data?.id;
    else {
      const latest = await api("GET", `/v1/services/${serviceId}/deploys?limit=1`);
      deployId = latest.data?.[0]?.deploy?.id;
    }
  }

  log("\n=== 8. Poll deploy status ===");
  let finalStatus = null;
  if (serviceId && deployId) {
    for (let i = 1; i <= 60; i++) {
      const d = await api("GET", `/v1/services/${serviceId}/deploys/${deployId}`);
      finalStatus = d.data?.status;
      log(`[${i}/60] Deploy status: ${finalStatus}`);
      if (["live", "deactivated", "build_failed", "update_failed", "canceled", "pre_deploy_failed"].includes(finalStatus)) break;
      await sleep(15000);
    }
  }

  log("\n=== DEPLOYMENT SUMMARY ===");
  log(`GitHub repo:   ${GITHUB_REPO_URL}`);
  log(`Service URL:   ${serviceUrl}`);
  log(`Dashboard URL: ${dashboardUrl}`);
  log(`Service ID:    ${serviceId}`);
  log(`Deploy ID:     ${deployId}`);
  log(`Deploy status: ${finalStatus}`);
  log(`Log file:      ${LOG_FILE}`);
}

main().catch((e) => {
  log("FATAL:", e.message);
  process.exit(1);
});