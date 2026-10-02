/*
 * Free SLP Gmail + Drive bridge. Runs only in the owner's personal Google
 * Apps Script account, using standard Apps Script / Gmail / Drive allowances.
 * No GCP billing, Cloud Scheduler, paid EPG or extra OAuth client is required
 * for this bridge. Existing SLP Cloud Run hosting is a SEPARATE cost risk.
 *
 * Script Properties, set manually before setup():
 *   SLP_BRIDGE_KEY = long random shared secret (>= 48 characters)
 *
 * Deploy as Web app: execute as ME, access ANYONE. All data-returning routes
 * require a fresh HMAC-SHA256 signature; never share the deployment URL/key.
 */
var SLP_FOLDER_NAME = "SLP_Private_EPG_Archive";
var SLP_MAX_ATTACH_BYTES = 6 * 1024 * 1024;
var SLP_MAX_FILES_PER_RUN = 30;
var SLP_MANIFEST_PAGE_SIZE = 25;

function slpProps_() { return PropertiesService.getScriptProperties(); }
function slpHex_(bytes) {
  return bytes.map(function(b) { return ("0" + (b & 255).toString(16)).slice(-2); }).join("");
}
function slpHash_(bytes) {
  return slpHex_(Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, bytes));
}
function slpKey_() {
  var key = slpProps_().getProperty("SLP_BRIDGE_KEY") || "";
  if (key.length < 48) throw new Error("SLP_BRIDGE_KEY missing or too short");
  return key;
}
function slpHmac_(message) {
  return slpHex_(Utilities.computeHmacSha256Signature(
    message, slpKey_(), Utilities.Charset.UTF_8
  ));
}
function slpEqual_(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== 64 ||
      b.length !== 64) return false;
  var mismatch = 0;
  for (var i = 0; i < a.length; i++) mismatch |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return mismatch === 0;
}
function slpAuthorize_(method, timestamp, operation, argument, signature) {
  var n = Number(timestamp);
  if (!Number.isFinite(n) || Math.abs(Date.now() - n) > 90000) return false;
  var input = method + "\n" + String(timestamp) + "\n" + operation + "\n" + argument;
  return slpEqual_(slpHmac_(input), signature);
}
function slpJson_(value) {
  return ContentService.createTextOutput(JSON.stringify(value))
      .setMimeType(ContentService.MimeType.JSON);
}
function slpFolder_() {
  var props = slpProps_(), id = props.getProperty("SLP_FOLDER_ID");
  if (id) return DriveApp.getFolderById(id);
  // No lookup by name: never accidentally reuse somebody else's shared folder.
  var folder = DriveApp.createFolder(SLP_FOLDER_NAME);
  props.setProperty("SLP_FOLDER_ID", folder.getId());
  return folder;
}
function slpManifest_() {
  var props = slpProps_(), id = props.getProperty("SLP_MANIFEST_ID");
  if (!id) {
    var file = slpFolder_().createFile(
      "slp_manifest.json", JSON.stringify({version:1,files:[]}), MimeType.PLAIN_TEXT
    );
    props.setProperty("SLP_MANIFEST_ID", file.getId());
    return {version:1,files:[]};
  }
  // Refuse to overwrite a corrupted manifest, preserving attachments.
  var data = JSON.parse(DriveApp.getFileById(id).getBlob().getDataAsString("UTF-8"));
  if (data.version !== 1 || !Array.isArray(data.files)) {
    throw new Error("SLP manifest invalid: stop instead of overwriting");
  }
  return data;
}
function slpSaveManifest_(manifest) {
  DriveApp.getFileById(slpProps_().getProperty("SLP_MANIFEST_ID"))
    .setContent(JSON.stringify(manifest));
}
function slpCandidate_(filename, subject, forwardedBody) {
  var name = String(filename || "");
  if (!/\.(xlsx|xls)$/i.test(name) || name.length > 180) return false;
  if (/(?:^|[^a-z0-9])setanta[\s_-]+(?:(?:sports)[\s_-]+)?(?:plus|kyrgyzstan|kyrgystan)\b/i.test(name)) return false;
  // Explicit supplier identities only. Do not fetch unrelated personal XLSX.
  if (/(setanta\s+sports\s*[12](?:\s+kazakhstan)?|setanta\s+(?:qazaqstan|kz)|q[\s_-]*(?:league|arena|football)|qazsport|sport\s*\+\s*qazaqstan|viju\s*\+\s*sport)/i.test(name)) {
    return true;
  }
  // Supplier's generic 'сетка Канала.xlsx' may still be important.
  // The real original sender, not merely the forwarding Gmail account, must
  // explicitly identify an approved supplier. Leave ambiguous docs out.
  return /(?:сетка\s*канала|расписани|programme|schedule|epg)/i.test(name)
    && /(?:sportplus|sportplustv|qazsporttv|silkwaymedia|setanta)/i.test(
      String(subject) + " " + String(forwardedBody).slice(0,700)
    );
}
function setup() {
  // Create the single shared bridge key automatically on first run.
  // Store it only in private Script Properties; do not expose it in logs.
  if (!slpProps_().getProperty("SLP_BRIDGE_KEY")) {
    slpProps_().setProperty(
      "SLP_BRIDGE_KEY", Utilities.getUuid() + Utilities.getUuid()
    );
  }
  slpKey_();
  slpFolder_();
  slpManifest_();
  var triggers = ScriptApp.getProjectTriggers();
  for (var i = 0; i < triggers.length; i++) {
    if (triggers[i].getHandlerFunction() === "syncMailbox") ScriptApp.deleteTrigger(triggers[i]);
  }
  ScriptApp.newTrigger("syncMailbox").timeBased().everyHours(1).create();
  syncMailbox();
  Logger.log("SLP configured: Gmail is archived to private Drive hourly.");
}
function syncMailbox() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(10000)) return;
  try {
    var manifest = slpManifest_();
    var existing = {};
    manifest.files.forEach(function(f) { existing[f.id] = true; });
    var accepted = 0;
    var seenMessages = {};
    var queries = [
      "newer_than:21d has:attachment {setanta qsport qazsport sportplus viju}",
      "newer_than:21d has:attachment {filename:xlsx filename:xls}"
    ];
    outer:
    for (var q = 0; q < queries.length; q++) {
      for (var offset = 0; offset < 150; offset += 30) {
        var threads = GmailApp.search(queries[q], offset, 30);
        if (!threads.length) break;
        for (var ti = 0; ti < threads.length; ti++) {
          var messages = threads[ti].getMessages();
          for (var mi = 0; mi < messages.length; mi++) {
            var msg = messages[mi];
            var mid = msg.getId();
            if (seenMessages[mid]) continue;
            seenMessages[mid] = true;
            var subject = msg.getSubject() || "";
            var body = msg.getPlainBody() || "";
            // Work-mail forwarding may use a plain "Отправитель: user@domain"
            // rather than angle brackets. Keep the true supplier identity.
            var original = body.match(/(?:^|\n)\s*(?:Отправитель|From|От)\s*:\s*([^\n\r]{1,300})/i);
            var originalAddress = original && original[1].match(/[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}/);
            var sender = originalAddress ? originalAddress[0] : msg.getFrom();
            var attachments = msg.getAttachments({includeInlineImages:false,includeAttachments:true});
            for (var ai = 0; ai < attachments.length; ai++) {
              var part = attachments[ai], filename = part.getName();
              if (!slpCandidate_(filename, subject, body)) continue;
              if (part.getSize() > SLP_MAX_ATTACH_BYTES || part.getSize() < 64) continue;
              var bytes = part.getBytes();
              var sha = slpHash_(bytes);
              var id = slpHash_(Utilities.newBlob(mid + "\n" + filename + "\n" + sha).getBytes())
                .slice(0,32);
              if (existing[id]) continue;
              var blob = Utilities.newBlob(bytes, part.getContentType(), filename);
              var saved = slpFolder_().createFile(blob);
              saved.setName("epg_" + id + "_" + filename.replace(/[^\w.а-яА-ЯёЁ+-]+/g, "_").slice(0,100));
              manifest.files.push({
                id:id, driveId:saved.getId(), filename:filename, messageId:mid,
                subject:subject.slice(0,320), sender:sender.slice(0,200),
                received:msg.getDate().toISOString(), sha256:sha, size:bytes.length
              });
              existing[id] = true;
              accepted++;
              // Persist progress incrementally to survive six-minute runtime.
              slpSaveManifest_(manifest);
              if (accepted >= SLP_MAX_FILES_PER_RUN) break outer;
            }
          }
        }
        if (threads.length < 30) break;
      }
    }
    slpProps_().setProperty("SLP_LAST_SCAN_AT", new Date().toISOString());
    Logger.log("SLP archived " + accepted + " new supplier attachments.");
  } finally {
    lock.releaseLock();
  }
}
function doGet(e) {
  try {
    var p = e.parameter || {}, op = String(p.op || ""), arg = "";
    if (op === "manifest") arg = String(p.offset || "0");
    else if (op === "file") arg = String(p.id || "");
    else if (op !== "backup" && op !== "template" && op !== "scan") {
      return slpJson_({ok:false,error:"unknown operation"});
    }
    if (!slpAuthorize_("GET", p.ts, op, arg, p.sig)) {
      return slpJson_({ok:false,error:"unauthorized"});
    }
    if (op === "scan") {
      syncMailbox();
      return slpJson_({
        ok:true,
        scanned:true,
        lastScan:slpProps_().getProperty("SLP_LAST_SCAN_AT") || ""
      });
    }
    if (op === "template") {
      var templateId = slpProps_().getProperty("SLP_TEMPLATE_ID");
      if (!templateId) return slpJson_({ok:true,exists:false});
      var tplBytes = DriveApp.getFileById(templateId).getBlob().getBytes();
      return slpJson_({ok:true,exists:true,sha256:slpHash_(tplBytes),
        data:Utilities.base64Encode(tplBytes)});
    }
    if (op === "backup") {
      var id = slpProps_().getProperty("SLP_BACKUP_ID");
      return slpJson_(id ? {
        ok:true,exists:true,
        sha256:slpProps_().getProperty("SLP_BACKUP_SHA") || "",
        ciphertext:DriveApp.getFileById(id).getBlob().getDataAsString("UTF-8")
      } : {ok:true,exists:false});
    }
    var manifest = slpManifest_();
    if (op === "manifest") {
      var offset = Number(arg);
      if (!Number.isInteger(offset) || offset < 0 || offset > 10000) {
        return slpJson_({ok:false,error:"invalid offset"});
      }
      // Stable append order lets SLP resume a large archive without shifting
      // page boundaries whenever new mail arrives. Never hide archived EPG
      // files older than 21 days from an SLP instance that was offline.
      var archived = manifest.files;
      return slpJson_({ok:true,files:archived.slice(offset,offset + SLP_MANIFEST_PAGE_SIZE)
        .map(function(f) {
          return {id:f.id,filename:f.filename,messageId:f.messageId,subject:f.subject,
            sender:f.sender,received:f.received,sha256:f.sha256,size:f.size};
        }),next:offset+SLP_MANIFEST_PAGE_SIZE < archived.length
          ? offset + SLP_MANIFEST_PAGE_SIZE : null,
        total:archived.length,
        lastScan:slpProps_().getProperty("SLP_LAST_SCAN_AT") || ""});
    }
    if (!/^[a-f0-9]{32}$/.test(arg)) return slpJson_({ok:false,error:"bad id"});
    var match = manifest.files.filter(function(f) {return f.id === arg;})[0];
    if (!match || match.size > SLP_MAX_ATTACH_BYTES) return slpJson_({ok:false,error:"not found"});
    var blobBytes = DriveApp.getFileById(match.driveId).getBlob().getBytes();
    if (blobBytes.length !== match.size || slpHash_(blobBytes) !== match.sha256) {
      return slpJson_({ok:false,error:"file integrity failure"});
    }
    return slpJson_({ok:true,id:match.id,sha256:match.sha256,
      data:Utilities.base64Encode(blobBytes)});
  } catch (err) {
    return slpJson_({ok:false,error:String(err).slice(0,120)});
  }
}
function doPost(e) {
  try {
    var p = e.parameter || {}, op = String(p.op || "");
    if (op !== "backup" && op !== "template") {
      return slpJson_({ok:false,error:"unknown operation"});
    }
    var body = e.postData ? e.postData.contents : "";
    if (!body || body.length > 14 * 1024 * 1024) return slpJson_({ok:false,error:"too large"});
    var digest = slpHash_(Utilities.newBlob(body).getBytes());
    if (!slpAuthorize_("POST", p.ts, op, digest, p.sig)) {
      return slpJson_({ok:false,error:"unauthorized"});
    }
    var payload = JSON.parse(body);
    if (op === "backup") {
      // Only encrypted Fernet tokens pass through this bridge; no raw SQLite.
      if (payload.version !== 1 || !/^[A-Za-z0-9_=-]+$/.test(payload.ciphertext || "") ||
          payload.ciphertext.length > 12 * 1024 * 1024) {
        return slpJson_({ok:false,error:"bad ciphertext"});
      }
    } else {
      if (payload.version !== 1 || !/^[A-Za-z0-9+/=]+$/.test(payload.data || "") ||
          payload.data.length > 8 * 1024 * 1024) {
        return slpJson_({ok:false,error:"bad template"});
      }
      var templateBytes = Utilities.base64Decode(payload.data);
      if (templateBytes.length > 6 * 1024 * 1024 ||
          slpHash_(templateBytes) !== payload.sha256) {
        return slpJson_({ok:false,error:"template integrity check failed"});
      }
    }
    var lock = LockService.getScriptLock();
    if (!lock.tryLock(15000)) return slpJson_({ok:false,error:"busy"});
    try {
      if (op === "template") {
        var templateProps = slpProps_();
        var currentTplSha = templateProps.getProperty("SLP_TEMPLATE_SHA") || "";
        if (currentTplSha !== String(payload.previousSha || "")) {
          return slpJson_({ok:false,error:"template changed concurrently"});
        }
        var oldTpl = templateProps.getProperty("SLP_TEMPLATE_ID");
        var newTpl = slpFolder_().createFile(Utilities.newBlob(
          templateBytes,
          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          "slp_approved_template.xlsx"
        ));
        templateProps.setProperties({
          "SLP_TEMPLATE_ID":newTpl.getId(),
          "SLP_TEMPLATE_SHA":payload.sha256
        });
        // Keep the previous uploaded template in Drive for audit/rollback.
        return slpJson_({ok:true,saved:true});
      }
      var props = slpProps_(), previous = props.getProperty("SLP_BACKUP_ID");
      var activeSha = props.getProperty("SLP_BACKUP_SHA") || "";
      if (String(payload.previousSha || "") !== activeSha) {
        return slpJson_({ok:false,error:"backup conflict: server must restore latest state"});
      }
      var created = slpFolder_().createFile(
        "slp_backup_" + new Date().toISOString() + ".encrypted",
        payload.ciphertext, MimeType.PLAIN_TEXT
      );
      props.setProperties({
        "SLP_BACKUP_ID":created.getId(),
        "SLP_BACKUP_SHA":slpHash_(Utilities.newBlob(payload.ciphertext).getBytes())
      });
      var older = props.getProperty("SLP_PREVIOUS_BACKUP_ID");
      props.setProperty("SLP_PREVIOUS_BACKUP_ID", previous || "");
      if (older && older !== previous) {
        try { DriveApp.getFileById(older).setTrashed(true); } catch (ignore) {}
      }
    } finally {
      lock.releaseLock();
    }
    return slpJson_({ok:true,saved:true});
  } catch (err) {
    return slpJson_({ok:false,error:String(err).slice(0,120)});
  }
}
