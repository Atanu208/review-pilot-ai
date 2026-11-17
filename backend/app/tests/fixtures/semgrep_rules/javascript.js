// Semgrep rule test fixture (annotations: "ruleid" must match, "ok" must not).
// Test data only; never executed.
const cp = require("child_process");
const crypto = require("crypto");

function code(input) {
  // ruleid: js.code-injection.eval
  eval(input);
  // ok: js.code-injection.eval
  eval("2 + 2");
  // ruleid: js.code-injection.eval
  const fn = new Function("a", input);
}

function dom(el, html) {
  // ruleid: js.xss.dom-sink
  el.innerHTML = html;
  // ok: js.xss.dom-sink
  el.innerHTML = "<b>static</b>";
  // ok: js.xss.dom-sink
  el.textContent = html;
  // ruleid: js.xss.dom-sink
  document.write(html);
}

function Comp({ html }) {
  // ruleid: js.xss.react-dangerously-set-inner-html
  return <div dangerouslySetInnerHTML={{ __html: html }} />;
}

function shell(branch) {
  // ruleid: js.command-injection.child-process
  cp.exec("git checkout " + branch);
  // ruleid: js.command-injection.child-process
  cp.execSync(`git log ${branch}`);
  // ok: js.command-injection.child-process
  cp.execFile("git", ["log", branch]);
}

async function sql(db, id) {
  // ruleid: js.sql-injection.string-built-query
  await db.query("SELECT * FROM users WHERE id = " + id);
  // ruleid: js.sql-injection.string-built-query
  await db.query(`SELECT * FROM users WHERE id = ${id}`);
  // ok: js.sql-injection.string-built-query
  await db.query("SELECT * FROM users WHERE id = $1", [id]);
}

// ruleid: js.tls.reject-unauthorized-disabled
const agentOptions = { rejectUnauthorized: false };
// ok: js.tls.reject-unauthorized-disabled
const safeOptions = { rejectUnauthorized: true };

// ruleid: js.secrets.hardcoded-credential
const apiKey = "sk_live_51HxyzABCDEFG";
// ok: js.secrets.hardcoded-credential
const apiKeyFromEnv = process.env.API_KEY;
// ok: js.secrets.hardcoded-credential
const password = "your-password-here";

function hash(data) {
  // ruleid: js.crypto.weak-hash
  crypto.createHash("md5").update(data);
  // ok: js.crypto.weak-hash
  crypto.createHash("sha256").update(data);
}
