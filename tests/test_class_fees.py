"""Class fees and their course are one submission, never half a purchase."""
import unittest
from unittest.mock import patch

from app.db import Database
from app.store import Store, ValidationError
from test_web import make_client
from test_mcp import build_mcp, run, tool_payload

COURSE = {"name": "羽毛球 / Badminton", "kind": "per_class", "class_count": 3,
          "period_label": "2026-10"}
FEE = {"date": "2026-10-28", "amount": "1000", "category": "aden-sports-classes",
       "description": "课程费", "submitted_by": "wife"}


class ClassFeeStoreTests(unittest.TestCase):
    def setUp(self):
        db = Database("sqlite:///:memory:")
        db.init()
        self.store = Store(db)
        self.expense = lambda eid: next(e for e in self.store.list() if e.id == eid)

    def counts(self):
        with self.store.db.tx() as tx:
            return [tx.query_one("SELECT COUNT(*) AS n FROM " + table)["n"]
                    for table in ("expenses", "class_packages", "expense_history")]

    def test_fee_without_course_refuses_without_writes(self):
        for cat in ("aden-edu-classes", "aden-sports-classes", " ADEN-SPORTS-CLASSES "):
            with self.subTest(cat):
                with self.assertRaisesRegex(ValidationError, "course"):
                    self.store.create(**dict(FEE, category=cat))
                self.assertEqual(self.counts(), [0, 0, 0])

    def test_valid_course_roundtrip_and_exact_money(self):
        e = self.store.create(**FEE, course=COURSE, paid=True, paid_date="2026-10-27")
        p, = self.store.list_packages()
        self.assertEqual(p["expense_id"], e.id)
        for key, value in COURSE.items():
            self.assertEqual(p[key], value)
        self.assertEqual(p["summary"]["rate"], 333.33)
        self.assertEqual(p["summary"]["remaining_amount"], 1000)
        saved = self.expense(e.id)
        self.assertEqual((saved.amount, saved.paid, saved.paid_date, saved.submitted_by),
                         (1000, True, "2026-10-27", "wife"))
        self.assertEqual(self.counts(), [1, 1, 2])
        self.assertEqual({h.action for h in self.store.history(e.id)}, {"create", "package_create"})
        self.assertEqual({h.changed_by for h in self.store.history(e.id)}, {"wife"})
        self.store.log_class(package_id=p["id"], kind="attended", date="2026-10-28")
        summary = self.store.package(p["id"])["summary"]
        self.assertEqual((summary["used_amount"], summary["remaining_amount"]), (333.33, 666.67))
        self.assertEqual(self.expense(e.id).amount, 1000)

    def test_invalid_course_never_leaves_an_expense(self):
        invalid = [None, {}, [], "bad", dict(COURSE, name="  "),
                   dict(COURSE, kind="bad"), dict(COURSE, class_count=0),
                   dict(COURSE, class_count=True), dict(COURSE, class_count=2.5),
                   dict(COURSE, class_count=1001), dict(COURSE, amount=900),
                   dict(COURSE, expense_id="other"), dict(COURSE, name={"bad": 1}),
                   dict(COURSE, period_label=["bad"])]
        for course in invalid:
            with self.subTest(course=course):
                with self.assertRaises(ValidationError):
                    self.store.create(**FEE, course=course)
                self.assertEqual(self.counts(), [0, 0, 0])

    def test_equipment_and_ordinary_expenses_still_need_no_course(self):
        for cat in ("aden-edu", "aden-sports", "home", "borrow", None):
            self.store.create(**dict(FEE, category=cat))
        self.assertEqual(self.counts(), [5, 0, 5])

    def test_course_on_non_fee_category_refuses(self):
        with self.assertRaisesRegex(ValidationError, "category"):
            self.store.create(**dict(FEE, category="aden-sports"), course=COURSE)
        self.assertEqual(self.counts(), [0, 0, 0])

    def test_failure_in_package_insert_rolls_back_everything(self):
        with patch.object(self.store, "_insert_package_in_tx", side_effect=RuntimeError("injected")):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                self.store.create(**FEE, course=COURSE)
        self.assertEqual(self.counts(), [0, 0, 0])

    def test_failure_after_both_inserts_rolls_back_both_and_history(self):
        original = self.store._write_history
        def fail(tx, eid, action, *args):
            if action == "package_create":
                raise RuntimeError("audit failure")
            return original(tx, eid, action, *args)
        with patch.object(self.store, "_write_history", side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, "audit failure"):
                self.store.create(**FEE, course=COURSE)
        self.assertEqual(self.counts(), [0, 0, 0])

    def test_mistaken_fee_deletion_explains_required_course_cleanup(self):
        e = self.store.create(**FEE, course=COURSE)
        p, = self.store.list_packages()
        for action in (lambda: self.store.delete(e.id), lambda: self.store.delete_package(p["id"])):
            with self.assertRaises(ValidationError) as error:
                action()
            message = str(error.exception)
            self.assertIn("其他", message)
            self.assertIn("expenses_update", message)
            self.assertIn("classes_delete", message)
        self.assertEqual(self.counts(), [1, 1, 2])

    def test_recategory_cannot_bypass_required_course(self):
        e = self.store.create(**dict(FEE, category="aden-sports"))
        with self.assertRaisesRegex(ValidationError, "course"):
            self.store.update(e.id, fields={"category": "aden-edu-classes", "amount": 1200})
        self.assertEqual((self.expense(e.id).category, self.expense(e.id).amount), ("aden-sports", 1000))
        p = self.store.create_package(expense_id=e.id, **COURSE)
        self.store.update(e.id, fields={"category": "aden-edu-classes"})
        with self.assertRaisesRegex(ValidationError, "class-fee"):
            self.store.delete_package(p["id"])
        self.assertEqual(self.counts(), [1, 1, 3])
        self.store.update_package(p["id"], fields={"archived": True})
        self.assertTrue(self.store.package(p["id"])["archived"])
        self.store.update(e.id, fields={"category": "aden-edu"})
        self.assertTrue(self.store.delete_package(p["id"]))


class ClassFeeBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.client, self.store, self.token = make_client()
        self.expense = lambda eid: next(e for e in self.store.list() if e.id == eid)

    def test_http_requires_course_and_attributes_both_to_link(self):
        body = dict(FEE, token=self.token, submitted_by="forged")
        self.assertEqual(self.client.post("/api/submit", json=body).status_code, 400)
        self.assertEqual(self.store.list(), [])
        response = self.client.post("/api/submit", json=dict(body, course=COURSE))
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        eid = result["expense"]["id"]
        self.assertEqual(result["package"]["class_count"], 3)
        self.assertEqual(result["package"]["name"], COURSE["name"])
        self.assertEqual({h.changed_by for h in self.store.history(eid)}, {"wife"})
        self.assertFalse(self.expense(eid).paid)
        self.assertEqual(len(self.store.list_packages()), 1)

    def test_submit_returns_created_snapshot_without_later_package_query(self):
        with patch.object(self.store, "packages_by_expense", side_effect=RuntimeError("later read failed")):
            result = self.client.post("/api/submit", json=dict(FEE, token=self.token, course=COURSE)).json()
            self.assertTrue(result["ok"])
            self.assertEqual(result["package"]["period_label"], "2026-10")
            self.assertEqual(result["package"]["summary"]["rate"], 333.33)
            mcp = build_mcp(self.store)
            second = tool_payload(run(mcp.call_tool("expenses_add", dict(FEE, course=COURSE))))
            self.assertEqual(second["package"]["expense_id"], second["id"])
            self.assertEqual(second["package"]["summary"]["amount"], 1000)
        self.assertEqual(len(self.store.list()), 2)
        self.assertEqual(len(self.store.list_packages()), 2)

    def test_mcp_same_rule_and_atomic_paid_course(self):
        mcp = build_mcp(self.store)
        with self.assertRaisesRegex(Exception, "course"):
            run(mcp.call_tool("expenses_add", FEE))
        self.assertEqual(self.store.list(), [])
        result = tool_payload(run(mcp.call_tool("expenses_add", dict(FEE, course=COURSE, paid=True))))
        self.assertEqual(result["package"]["class_count"], 3)
        self.assertTrue(self.expense(result["id"]).paid)
        self.assertEqual(len(self.store.history(result["id"])), 2)


class ClassFeePortalTests(unittest.TestCase):
    """Execute the shipping helpers and handler, with real asynchronous promises."""
    def js(self, scenario):
        import json
        import subprocess
        from pathlib import Path
        source = (Path(__file__).resolve().parent.parent / "app/portal.html").read_text()
        strings = source[source.index('  var STR = {'):source.index('  var lang =')]
        money = source[source.index('  function money(n) {'):source.index('  function money0(n) {')]
        category = source[source.index('  function categoryLabel(e) {'):source.index('  function isBorrow(e) {')]
        block = source[source.index('  function isClassFee('):source.index('  // ---- per-item actions ----')]
        script = '''
const assert = require('node:assert/strict');
var nodes={}, calls=[], messages=[], listeners={}, refreshCount=0, classRefreshCount=0;
function $(id) {
  if (!nodes[id]) nodes[id]={value:'', hidden:false, disabled:false, textContent:'',
    addEventListener: function(event, fn) {listeners[id+':'+event]=fn;}};
  return nodes[id];
}
var lang='en';
function t(key) {return STR[lang][key] || key;}
function todayStr() {return '2026-10-28';}
function toast(s) {messages.push(s);}
function refresh() {refreshCount++;}
function refreshClasses() {classRefreshCount++;}
let resolveWrite, rejectWrite;
function api(name, body) {calls.push({name,body});return new Promise((res,rej)=>{resolveWrite=res;rejectWrite=rej;});}
function submit() {listeners['addForm:submit']({preventDefault(){}});}
function fields() {
  const values={amount:'1000',date:'2026-10-28',category:'aden-sports-classes',desc:'课费',
    feeName:' 羽毛球 ',feeCount:'3',feeKind:'per_class',feePeriod:'old period'};
  for (const [key,value] of Object.entries(values)) $(key).value=value;
}
async function settle() {await Promise.resolve();await Promise.resolve();}
''' + strings + money + category + block + '\n(async()=>{' + scenario + '\n})().catch(e=>{console.error(e);process.exitCode=1;});'
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_switching_categories_controls_required_course_and_rate_preview(self):
        self.js('''fields();feeCourseFields();
assert.equal($('feeCourse').hidden,false);assert.equal($('feeCourse').disabled,false);
assert.equal($('feePeriod').disabled,true);
assert.match($('feeRateHint').textContent,/333.33/);
$('feeKind').value='period';listeners['feeKind:change']();
assert.equal($('feePeriodWrap').hidden,false);assert.equal($('feePeriod').disabled,false);
$('category').value='aden-sports';listeners['category:change']();
assert.equal($('feeCourse').hidden,true);assert.equal($('feeCourse').disabled,true);
submit();assert.equal(calls.length,1);assert.equal(calls[0].body.course,undefined);
''')

    def test_invalid_course_and_fractional_count_never_submit(self):
        self.js('''fields();
for(const count of ['', '0', '-1', '1.5', '1001']) {
 $('feeCount').value=count;submit();assert.equal(calls.length,0);
}
$('feeCount').value='3';$('feeName').value='  ';submit();assert.equal(calls.length,0);
$('feeName').value='羽毛球';$('feeKind').value='bad';submit();assert.equal(calls.length,0);
''')

    def test_single_submission_and_confirmation_use_server_course(self):
        self.js('''fields();submit();submit();assert.equal(calls.length,1);
assert.equal($('addSave').disabled,true);assert.equal($('addFields').disabled,true);
assert.deepEqual(calls[0],{name:'submit',body:{date:'2026-10-28',amount:1000,
 category:'aden-sports-classes',description:'课费',course:{name:'羽毛球',kind:'per_class',class_count:3,period_label:null}}});
resolveWrite({ok:true,expense:{date:'2026-10-29',amount:999,description:'stored'},
 package:{name:'Stored course',class_count:4}});await settle();
assert.match(messages[0],/Stored course/);assert.match(messages[0],/4/);
assert.doesNotMatch(messages[0],/羽毛球/);
assert.equal($('feeName').value,'');assert.equal($('amount').value,'');
assert.equal(refreshCount,1);assert.equal(classRefreshCount,1);assert.equal($('addSave').disabled,false);
assert.equal($('addFields').disabled,false);
''')

    def test_explicit_refusal_preserves_fields_and_allows_correction(self):
        self.js('''fields();submit();rejectWrite({answered:true,message:'invalid course'});await settle();
assert.equal($('feeName').value,' 羽毛球 ');assert.equal($('amount').value,'1000');
assert.equal($('addSave').disabled,false);assert.equal($('addFields').disabled,false);assert.equal(refreshCount,0);
assert.deepEqual(messages,['invalid course']);submit();assert.equal(calls.length,2);
''')

    def test_unknown_write_result_stays_locked_until_reload(self):
        self.js('''fields();submit();rejectWrite(new Error('connection lost'));await settle();submit();
assert.equal(calls.length,1);assert.equal($('addSave').disabled,true);assert.equal($('addFields').disabled,true);
assert.equal($('feeName').value,' 羽毛球 ');assert.equal(refreshCount,0);
assert.match($('addStatus').textContent,/Reload and check/);
''')

    def test_period_course_sends_period_label(self):
        self.js('''fields();$('category').value='aden-edu-classes';$('feeKind').value='period';
$('feePeriod').value='  秋季  ';submit();
assert.equal(calls[0].body.course.kind,'period');assert.equal(calls[0].body.course.period_label,'秋季');
''')
