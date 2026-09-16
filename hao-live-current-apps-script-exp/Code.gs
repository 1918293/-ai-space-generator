const HAO_SCHEMA_VERSION = '0.3.0-exp';
const HAO_SPREADSHEET_PROPERTY = 'HAO_SYSTEM_SPREADSHEET_ID';

const CURRENT_POINTERS = Object.freeze({
  DEFAULT_ACTION: 419,
  SYS_OPERATION_RULE: 525,
  MODE_LOCAL_OPERATION_RULE: 533,
  ACTION_ADMISSION_BINDING: 540,
  CONTEXT_CHECKPOINT_RULE: 560,
});

function doGet() {
  return HtmlService.createTemplateFromFile('Index')
    .evaluate()
    .setTitle('Hao System · Private Formal Current')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1');
}

function getPrivateFormalCurrent() {
  const ss = openHaoSpreadsheet_();
  const dashboard = ss.getSheetByName('00_Dashboard');
  const config = ss.getSheetByName('06_Config');
  if (!dashboard || !config) throw new Error('HAO_REQUIRED_SHEET_MISSING');

  const dashboardValues = dashboard.getRange('A1:E26').getDisplayValues();
  const controls = {};
  Object.keys(CURRENT_POINTERS).forEach((key) => {
    controls[key] = readCurrentControl_(config, key, CURRENT_POINTERS[key]);
  });

  return {
    schemaVersion: HAO_SCHEMA_VERSION,
    artifactRole: 'PRIVATE_FORMAL_CURRENT_PROJECTION',
    formalAuthority: 'Google Drive',
    authorityMutation: false,
    systemAdmin: {
      dashboardCountSemantics: 'RAW_INTAKE_LIFECYCLE_COUNTS',
      currentActionableWorkload: 'EXTERNAL_RESOLUTION_REQUIRED',
      runtimeHealth: 'EXTERNAL_PROVIDER_READ_REQUIRED',
      ciHealth: 'EXTERNAL_PROVIDER_READ_REQUIRED',
      maintenanceMutation: 'NOT_PERFORMED_BY_THIS_READ_ONLY_APP'
    },
    conversationCurrent: {
      status: 'EXTERNAL_REQUIRED',
      note: 'Mode/TASK are resolved by ChatGPT current conversation, not by this web app.'
    },
    system: parseDashboard_(dashboardValues),
    controls,
    freshness: {
      readAt: new Date().toISOString(),
      source: 'GOOGLE_APPS_SCRIPT_DIRECT_PRIVATE_READ',
      spreadsheetIdExposedToClient: false,
      credentialsExposedToClient: false
    }
  };
}

function getPrivateProjectIndex(indexId) {
  if (!/^IDX-\d{3}$/.test(String(indexId || ''))) {
    throw new Error('HAO_PROJECT_INDEX_ID_INVALID');
  }

  const ss = openHaoSpreadsheet_();
  const indexSheet = ss.getSheetByName('07_System_Index');
  if (!indexSheet) throw new Error('HAO_SYSTEM_INDEX_SHEET_MISSING');

  const lastRow = Math.max(indexSheet.getLastRow(), 2);
  const matches = indexSheet.getRange(`A1:A${lastRow}`)
    .createTextFinder(indexId)
    .matchEntireCell(true)
    .findAll();

  if (matches.length === 0) throw new Error(`HAO_PROJECT_INDEX_NOT_FOUND:${indexId}`);
  if (matches.length > 1) throw new Error(`HAO_PROJECT_INDEX_AMBIGUOUS:${indexId}`);

  const resolvedRow = matches[0].getRow();
  const row = indexSheet.getRange(resolvedRow, 1, 1, 12).getDisplayValues()[0];
  if (row[0] !== indexId) throw new Error(`HAO_PROJECT_INDEX_MISMATCH:${indexId}`);

  return {
    schemaVersion: HAO_SCHEMA_VERSION,
    artifactRole: 'PRIVATE_PROJECT_INDEX_PROJECTION',
    formalAuthority: 'Google Drive',
    authorityMutation: false,
    project: {
      indexId: row[0],
      title: row[1],
      primaryDomain: row[3],
      secondaryRole: row[4],
      authorityLevel: row[5],
      lifecycleState: row[6],
      evidenceClass: row[7],
      action: row[8],
      url: row[9],
      indexedAt: row[11],
      resolvedRow,
    },
    privacy: {
      fileIdExposedToClient: false,
      rawNotesExposedToClient: false,
      credentialsExposedToClient: false,
    },
    freshness: {
      readAt: new Date().toISOString(),
      source: 'GOOGLE_APPS_SCRIPT_DIRECT_PRIVATE_READ'
    }
  };
}

function openHaoSpreadsheet_() {
  const spreadsheetId = getRequiredScriptProperty_(HAO_SPREADSHEET_PROPERTY);
  return SpreadsheetApp.openById(spreadsheetId);
}

function readCurrentControl_(sheet, expectedKey, expectedRow) {
  let row = sheet.getRange(expectedRow, 1, 1, 6).getDisplayValues()[0];
  let resolvedRow = expectedRow;

  if (!isValidCurrentControlRow_(row, expectedKey)) {
    const lastRow = Math.max(sheet.getLastRow(), expectedRow);
    const matches = sheet.getRange(`A1:A${lastRow}`)
      .createTextFinder(expectedKey)
      .matchEntireCell(true)
      .findAll();

    const currentCandidates = matches
      .map((match) => {
        const candidateRowNumber = match.getRow();
        const candidateRow = sheet.getRange(candidateRowNumber, 1, 1, 6).getDisplayValues()[0];
        return { candidateRowNumber, candidateRow };
      })
      .filter(({ candidateRow }) => isValidCurrentControlRow_(candidateRow, expectedKey));

    if (currentCandidates.length === 0) {
      throw new Error(`HAO_CURRENT_CONTROL_NOT_FOUND:${expectedKey}`);
    }
    if (currentCandidates.length > 1) {
      throw new Error(`HAO_CURRENT_CONTROL_AMBIGUOUS:${expectedKey}`);
    }

    resolvedRow = currentCandidates[0].candidateRowNumber;
    row = currentCandidates[0].candidateRow;
  }

  if (!isValidCurrentControlRow_(row, expectedKey)) {
    throw new Error(`HAO_CURRENT_CONTROL_INVALID:${expectedKey}`);
  }

  return {
    key: row[0],
    state: row[2],
    authority: row[3],
    updatedAt: row[4],
    resolvedRow,
    pointerStatus: resolvedRow === expectedRow ? 'EXPECTED_POINTER' : 'TARGETED_RE_RESOLVED'
  };
}

function isValidCurrentControlRow_(row, expectedKey) {
  return Boolean(
    row &&
    row[0] === expectedKey &&
    row[2] === 'CURRENT' &&
    row[3] === 'Hao'
  );
}

function parseDashboard_(rows) {
  const metric = (label) => {
    const row = rows.find((r) => r[0] === label);
    return row ? row[1] : null;
  };

  return {
    title: rows[0]?.[0] || 'Hao System',
    status: rows[0]?.[2] || null,
    activatedAt: rows[0]?.[4] || null,
    counts: {
      intake: numberOrNull_(metric('全部收件')),
      readBackOk: numberOrNull_(metric('Intake 直接 READ_BACK_OK')),
      pending: numberOrNull_(metric('待處理')),
      blocked: numberOrNull_(metric('阻塞')),
      classificationCorrections: numberOrNull_(metric('分類修正')),
    },
    currentNavigation: metric('Current 導航'),
    taskSource: metric('Current TASK 來源'),
    projectPointers: rows
      .filter((r) => /^IDX-\d+$/.test(r[2] || ''))
      .map((r) => ({ label: r[0], action: r[1], indexId: r[2] }))
  };
}

function numberOrNull_(value) {
  if (value === null || value === '') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function getRequiredScriptProperty_(key) {
  const value = PropertiesService.getScriptProperties().getProperty(key);
  if (!value) throw new Error(`HAO_REQUIRED_SCRIPT_PROPERTY_MISSING:${key}`);
  return value;
}
