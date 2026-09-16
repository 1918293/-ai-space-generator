import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const code = fs.readFileSync(new URL('./Code.gs', import.meta.url), 'utf8');

function dashboardRows() {
  const rows = Array.from({ length: 26 }, () => ['', '', '', '', '']);
  rows[0] = ['Hao System｜完整收件與可驗證保存系統｜v1.0', '狀態', 'ACTIVE', '啟用時間', '2026-07-30T17:18+08:00'];
  rows[2] = ['全部收件', '715', '所有已登錄輸入', '', ''];
  rows[3] = ['Intake 直接 READ_BACK_OK', '165', '', '', ''];
  rows[4] = ['待處理', '2', '', '', ''];
  rows[5] = ['阻塞', '6', '', '', ''];
  rows[6] = ['分類修正', '46', '', '', ''];
  rows[11] = ['Current 導航', 'Master Index v1.0.2', '', '', ''];
  rows[17] = ['Current TASK 來源', '最新 Hao 明確指令 + formal Current/Handoff resolution', '', '', ''];
  rows[23] = ['Lofty', '開啟 Lofty Project Index', 'IDX-048', '', ''];
  return rows;
}

const POINTERS = {
  DEFAULT_ACTION: 419,
  SYS_OPERATION_RULE: 525,
  MODE_LOCAL_OPERATION_RULE: 533,
  ACTION_ADMISSION_BINDING: 540,
  CONTEXT_CHECKPOINT_RULE: 560,
};

function validRow(key, updatedAt = '2026-09-16T18:20:14+08:00') {
  return [key, 'VALUE', 'CURRENT', 'Hao', updatedAt, 'notes'];
}

function makeRuntime({
  property = 'synthetic-spreadsheet-id',
  dashboard = dashboardRows(),
  rows = {},
  lastRow = 600,
  missingDashboard = false,
  missingConfig = false,
} = {}) {
  const configRows = new Map();
  Object.entries(POINTERS).forEach(([key, rowNum]) => configRows.set(rowNum, validRow(key)));
  Object.entries(rows).forEach(([rowNum, row]) => configRows.set(Number(rowNum), row));

  const rangeForConfig = (...args) => {
    if (typeof args[0] === 'string') {
      const a1 = args[0];
      return {
        createTextFinder(search) {
          return {
            matchEntireCell() { return this; },
            findAll() {
              const matches = [];
              for (const [rowNum, row] of configRows.entries()) {
                if (row[0] === search) matches.push({ getRow: () => rowNum });
              }
              return matches.sort((a, b) => a.getRow() - b.getRow());
            },
          };
        },
      };
    }
    const [rowNum] = args;
    return {
      getDisplayValues() {
        return [configRows.get(rowNum) ?? ['', '', '', '', '', '']];
      },
    };
  };

  const spreadsheet = {
    getSheetByName(name) {
      if (name === '00_Dashboard') {
        if (missingDashboard) return null;
        return { getRange: () => ({ getDisplayValues: () => dashboard }) };
      }
      if (name === '06_Config') {
        if (missingConfig) return null;
        return {
          getRange: rangeForConfig,
          getLastRow: () => lastRow,
        };
      }
      return null;
    },
  };

  const context = vm.createContext({
    console,
    Date,
    Number,
    Boolean,
    Object,
    Array,
    Math,
    RegExp,
    JSON,
    Error,
    PropertiesService: {
      getScriptProperties: () => ({ getProperty: () => property }),
    },
    SpreadsheetApp: {
      openById(id) {
        assert.equal(id, property);
        return spreadsheet;
      },
    },
    HtmlService: {},
  });
  vm.runInContext(code, context, { filename: 'Code.gs' });
  return context;
}

function expectThrows(fn, pattern) {
  let threw = false;
  try { fn(); } catch (error) {
    threw = true;
    assert.match(String(error.message), pattern);
  }
  assert.equal(threw, true, `Expected throw matching ${pattern}`);
}

const results = [];
function test(name, fn) {
  try {
    fn();
    results.push({ name, pass: true });
  } catch (error) {
    results.push({ name, pass: false, error: String(error?.stack || error) });
  }
}

test('happy path returns private formal current without credential/id leakage', () => {
  const ctx = makeRuntime();
  const out = ctx.getPrivateFormalCurrent();
  assert.equal(out.formalAuthority, 'Google Drive');
  assert.equal(out.authorityMutation, false);
  assert.equal(out.system.status, 'ACTIVE');
  assert.equal(out.system.counts.pending, 2);
  assert.equal(out.system.counts.blocked, 6);
  assert.equal(out.system.projectPointers[0].indexId, 'IDX-048');
  assert.equal(out.controls.ACTION_ADMISSION_BINDING.pointerStatus, 'EXPECTED_POINTER');
  assert.equal(out.conversationCurrent.status, 'EXTERNAL_REQUIRED');
  const serialized = JSON.stringify(out);
  assert.equal(serialized.includes('synthetic-spreadsheet-id'), false);
  assert.equal(serialized.includes('oauth'), false);
});

test('stale pointer re-resolves to the unique CURRENT Hao row even with historical duplicate', () => {
  const ctx = makeRuntime({
    rows: {
      540: ['ACTION_ADMISSION_BINDING', 'OLD', 'SUPERSEDED', 'Hao', '2026-09-01', 'historical'],
      575: validRow('ACTION_ADMISSION_BINDING', '2026-09-16T22:00+08:00'),
    },
  });
  const out = ctx.getPrivateFormalCurrent();
  assert.equal(out.controls.ACTION_ADMISSION_BINDING.resolvedRow, 575);
  assert.equal(out.controls.ACTION_ADMISSION_BINDING.pointerStatus, 'TARGETED_RE_RESOLVED');
});

test('zero CURRENT candidates fails closed', () => {
  const ctx = makeRuntime({
    rows: {
      540: ['ACTION_ADMISSION_BINDING', 'OLD', 'SUPERSEDED', 'Hao', '2026-09-01', 'historical'],
    },
  });
  expectThrows(() => ctx.getPrivateFormalCurrent(), /HAO_CURRENT_CONTROL_NOT_FOUND:ACTION_ADMISSION_BINDING/);
});

test('multiple CURRENT Hao candidates fail closed as ambiguous', () => {
  const ctx = makeRuntime({
    rows: {
      540: ['ACTION_ADMISSION_BINDING', 'OLD', 'SUPERSEDED', 'Hao', '2026-09-01', 'historical'],
      575: validRow('ACTION_ADMISSION_BINDING'),
      580: validRow('ACTION_ADMISSION_BINDING'),
    },
  });
  expectThrows(() => ctx.getPrivateFormalCurrent(), /HAO_CURRENT_CONTROL_AMBIGUOUS:ACTION_ADMISSION_BINDING/);
});

test('wrong authority candidate is rejected', () => {
  const ctx = makeRuntime({
    rows: {
      540: ['ACTION_ADMISSION_BINDING', 'VALUE', 'CURRENT', 'Other', '2026-09-16', 'bad authority'],
    },
  });
  expectThrows(() => ctx.getPrivateFormalCurrent(), /HAO_CURRENT_CONTROL_NOT_FOUND:ACTION_ADMISSION_BINDING/);
});

test('missing Script Property fails before spreadsheet access', () => {
  const ctx = makeRuntime({ property: '' });
  expectThrows(() => ctx.getPrivateFormalCurrent(), /HAO_REQUIRED_SCRIPT_PROPERTY_MISSING:HAO_SYSTEM_SPREADSHEET_ID/);
});

test('missing required sheet fails closed', () => {
  const ctx = makeRuntime({ missingConfig: true });
  expectThrows(() => ctx.getPrivateFormalCurrent(), /HAO_REQUIRED_SHEET_MISSING/);
});

const failed = results.filter((r) => !r.pass);
console.log(JSON.stringify({
  event: 'hao_private_live_current_runtime_behavior',
  passed: results.length - failed.length,
  total: results.length,
  result: failed.length ? 'FAIL' : 'PASS',
  cases: results,
}, null, 2));
if (failed.length) process.exit(1);
