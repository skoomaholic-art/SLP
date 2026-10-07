// Runs the real Apps Script bridge source under Node with in-memory stand-ins
// for the Apps Script services. Input (stdin): {props, failFor, calls:[...]}.
// Output (stdout): {responses:[...], mails:[...], props:{...}, cache:{...}}.
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const input = JSON.parse(fs.readFileSync(0, "utf8"));
const props = Object.assign({}, input.props || {});
const cache = Object.assign({}, input.cache || {});
const mails = [];
const signed = buffer => Array.from(buffer).map(b => (b > 127 ? b - 256 : b));

const sandbox = {
  PropertiesService: {getScriptProperties: () => ({
    getProperty: key => (key in props ? props[key] : null),
    setProperty: (key, value) => { props[key] = String(value); },
    setProperties: values => Object.assign(props, values),
  })},
  CacheService: {getScriptCache: () => ({
    get: key => (key in cache ? cache[key] : null),
    put: (key, value) => { cache[key] = value; },
  })},
  LockService: {getScriptLock: () => ({tryLock: () => true, releaseLock: () => {}})},
  Utilities: {
    DigestAlgorithm: {SHA_256: "sha256"},
    Charset: {UTF_8: "utf8"},
    computeDigest: (algorithm, bytes) => signed(
      crypto.createHash("sha256").update(Buffer.from(bytes.map(b => b & 255))).digest()),
    computeHmacSha256Signature: (message, key) => signed(
      crypto.createHmac("sha256", Buffer.from(key, "utf8"))
        .update(Buffer.from(message, "utf8")).digest()),
    newBlob: value => ({getBytes: () => signed(Buffer.from(String(value), "utf8"))}),
    base64Encode: bytes => Buffer.from(bytes.map(b => b & 255)).toString("base64"),
    base64Decode: text => signed(Buffer.from(text, "base64")),
  },
  ContentService: {
    MimeType: {JSON: "json"},
    createTextOutput: text => ({text, setMimeType() { return this; }}),
  },
  GmailApp: {
    sendEmail: (to, subject, body) => {
      if ((input.failFor || []).includes(to)) throw new Error("Service invoked too many times");
      mails.push({to, subject, body});
    },
    search: () => [],
  },
  DriveApp: {}, ScriptApp: {getProjectTriggers: () => []},
  MimeType: {PLAIN_TEXT: "text/plain"},
  Logger: {log: () => {}},
};

const source = fs.readFileSync(path.join(
  __dirname, "..", "integrations", "google_apps_script", "SLP_Free_Gmail_Drive.gs"), "utf8");
const names = Object.keys(sandbox);
const api = new Function(...names, source + "\nreturn {doGet, doPost};")(
  ...names.map(name => sandbox[name]));

const responses = (input.calls || []).map(call => {
  const event = {parameter: call.params || {}};
  if (call.method === "POST") event.postData = {contents: call.body};
  const output = call.method === "POST" ? api.doPost(event) : api.doGet(event);
  return JSON.parse(output.text);
});
process.stdout.write(JSON.stringify({responses, mails, props, cache}));
