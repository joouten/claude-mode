#!/usr/bin/env node

const fs = require("fs");
const path = require("path");

const PKG_ROOT = path.resolve(__dirname, "..");
const TARGET = process.cwd();

const COMMANDS_SRC = path.join(PKG_ROOT, ".claude", "commands");
const TEMPLATES_SRC = path.join(PKG_ROOT, "templates");

const COMMANDS_DST = path.join(TARGET, ".claude", "commands");
const GITIGNORE_PATH = path.join(TARGET, ".gitignore");

const GITIGNORE_LINES = ["/CHAT_TO_CODE.md", "/CODE_TO_CHAT.md"];

function log(msg) {
  process.stdout.write(msg + "\n");
}

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function copyIfMissing(src, dst, label) {
  if (fs.existsSync(dst)) {
    log("  skip   " + label + " (already exists, not overwriting)");
    return false;
  }
  fs.copyFileSync(src, dst);
  log("  wrote  " + label);
  return true;
}

function installCommands() {
  log("Installing slash commands -> .claude/commands/");
  ensureDir(COMMANDS_DST);
  for (const file of ["mode-cc.md", "mode-c.md"]) {
    copyIfMissing(
      path.join(COMMANDS_SRC, file),
      path.join(COMMANDS_DST, file),
      ".claude/commands/" + file
    );
  }
}

function installTemplates() {
  log("Installing handoff templates -> project root");
  for (const file of ["CHAT_TO_CODE.md", "CODE_TO_CHAT.md"]) {
    copyIfMissing(
      path.join(TEMPLATES_SRC, file),
      path.join(TARGET, file),
      file
    );
  }
}

function updateGitignore() {
  log("Updating .gitignore");
  let existing = "";
  if (fs.existsSync(GITIGNORE_PATH)) {
    existing = fs.readFileSync(GITIGNORE_PATH, "utf8");
  }
  const existingLines = new Set(
    existing.split(/\r?\n/).map((l) => l.trim()).filter(Boolean)
  );
  const toAppend = GITIGNORE_LINES.filter((l) => !existingLines.has(l));
  if (toAppend.length === 0) {
    log("  skip   .gitignore (entries already present)");
    return;
  }
  const sep = existing.length === 0 || existing.endsWith("\n") ? "" : "\n";
  const block =
    sep +
    (existing.length === 0 ? "" : "\n") +
    "# claude-mode generated files — do not commit\n" +
    toAppend.join("\n") +
    "\n";
  fs.appendFileSync(GITIGNORE_PATH, block);
  log("  wrote  " + toAppend.length + " entr" + (toAppend.length === 1 ? "y" : "ies") + " to .gitignore");
}

function main() {
  log("claude-mode init -> " + TARGET);
  installCommands();
  installTemplates();
  updateGitignore();
  log("");
  log("claude-mode installed. Run /project:mode-cc to start.");
}

function usage() {
  process.stdout.write(
    "Usage: claude-mode init\n" +
      "\n" +
      "Installs claude-mode slash commands and handoff templates into the\n" +
      "current directory. Run from the root of the project you want to set up.\n"
  );
}

const subcommand = process.argv[2];

if (subcommand === "init") {
  try {
    main();
  } catch (err) {
    process.stderr.write("claude-mode init failed: " + err.message + "\n");
    process.exit(1);
  }
} else {
  usage();
  process.exit(subcommand ? 1 : 0);
}
