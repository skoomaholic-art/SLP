/*
 * SLP Render automatic pairing helper.
 *
 * Add as a NEW file named Pairing.gs in the ALREADY DEPLOYED
 * Google Apps Script project (leave Code.gs untouched).
 * Run prepareSLPConnection() once in the Apps Script editor.
 *
 * This publishes NO endpoint and never displays or logs the key.
 * The handoff is a temporary plaintext file in the owner's
 * already-private SLP Drive folder. The ChatGPT Google Drive connector
 * will read the file, privately configure existing Render environment,
 * and permanently delete it. Do not share the folder or the file.
 */
function prepareSLPConnection() {
  var props = PropertiesService.getScriptProperties();
  var key = props.getProperty("SLP_BRIDGE_KEY") || "";
  var url = ScriptApp.getService().getUrl() || "";
  if (key.length < 48) {
    throw new Error("The original SLP setup has not created its bridge key");
  }
  if (!/^https:\/\/script\.google\.com\/macros\/s\/[A-Za-z0-9_-]+\/exec$/.test(url)) {
    throw new Error(
      "This project has no published /exec web app. Check Manage deployments."
    );
  }
  // Reuse the exact folder and key from the same Script project.
  var folderId = props.getProperty("SLP_FOLDER_ID");
  if (!folderId) {
    throw new Error("The original SLP setup has not created its Drive folder");
  }
  var folder = DriveApp.getFolderById(folderId);
  if (folder.getSharingAccess() !== DriveApp.Access.PRIVATE) {
    throw new Error("The SLP folder is shared. Do not write a secret to it.");
  }
  var name = "slp_render_pairing.json";
  var current = folder.getFilesByName(name);
  while (current.hasNext()) {
    // Do not leave an older credential-bearing response active.
    current.next().setTrashed(true);
  }
  var now = Date.now();
  var payload = {
    version: 1,
    purpose: "pair_existing_free_render_with_slp_drive",
    createdAt: new Date(now).toISOString(),
    expiresAt: new Date(now + 20 * 60000).toISOString(),
    scriptUrl: url,
    secret: key
  };
  folder.createFile(name, JSON.stringify(payload), MimeType.PLAIN_TEXT);
  Logger.log(
    "SLP pairing is ready in your private Google Drive folder. " +
    "Do not copy or share any links or secret values."
  );
}
