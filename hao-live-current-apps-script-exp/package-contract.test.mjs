import fs from 'node:fs';
import assert from 'node:assert/strict';

const root = 'hao-live-current-apps-script-exp';
const code = fs.readFileSync(`${root}/Code.gs`, 'utf8');
const html = fs.readFileSync(`${root}/Index.html`, 'utf8');
const manifest = JSON.parse(fs.readFileSync(`${root}/appsscript.json`, 'utf8'));
const all = `${code}\n${html}\n${JSON.stringify(manifest)}`;

assert.equal(manifest.webapp.access, 'MYSELF');
assert.equal(manifest.webapp.executeAs, 'USER_DEPLOYING');
assert.deepEqual(manifest.oauthScopes, ['https://www.googleapis.com/auth/spreadsheets.readonly']);

assert.match(code, /HAO_SCHEMA_VERSION = '0\.3\.0-exp'/);
assert.match(code, /PropertiesService\.getScriptProperties\(\)/);
assert.match(code, /SpreadsheetApp\.openById\(spreadsheetId\)/);
assert.match(code, /authorityMutation:\s*false/);
assert.match(code, /RAW_INTAKE_LIFECYCLE_COUNTS/);
assert.match(code, /EXTERNAL_PROVIDER_READ_REQUIRED/);
assert.match(code, /NOT_PERFORMED_BY_THIS_READ_ONLY_APP/);
assert.match(code, /EXTERNAL_REQUIRED/);
assert.match(code, /createTextFinder\(expectedKey\)/);
assert.match(code, /matchEntireCell\(true\)/);

assert.match(html, />System Admin</);
assert.match(html, /Pending · Raw Intake/);
assert.match(html, /Blocked · Raw Intake/);
assert.match(html, /They are not the resolved Current workload/);
assert.match(html, /fresh provider state/);

const denied = [
  'ANYONE_ANONYMOUS',
  '"ANYONE"',
  'ScriptApp.getOAuthToken',
  'setValue(',
  'setValues(',
  'appendRow(',
  'clearContent(',
  'deleteRow(',
  'deleteColumn(',
  'insertRow(',
  'batchUpdate',
  'https://docs.google.com/spreadsheets/d/1xWsMmZ6ypUg0lswlfojnQdUp-c7DF8zto7Sz8alTFTk',
  '1xWsMmZ6ypUg0lswlfojnQdUp-c7DF8zto7Sz8alTFTk'
];
for (const token of denied) assert.equal(all.includes(token), false, `denied token leaked: ${token}`);

assert.equal(html.includes('innerHTML ='), false, 'UI must render fresh data with textContent, not innerHTML');

console.log(JSON.stringify({
  event: 'hao_private_live_current_app_contract',
  result: 'PASS',
  access: manifest.webapp.access,
  executeAs: manifest.webapp.executeAs,
  oauthScopes: manifest.oauthScopes,
  spreadsheetIdInRepo: false,
  mutationSurface: false,
  conversationCurrentAuthority: false,
  systemAdminRawLifecycleSemantics: true
}));
