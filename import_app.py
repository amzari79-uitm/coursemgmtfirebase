"""Streamlit app: import JSON sample data into Firestore.

Run:  streamlit run import_app.py
Accepts   (a) one combined file  {"users": {id: doc}, "courses": {...}, "sessions": {...}, "enrollments": {...}}
          (b) separate files named users.json / courses.json / sessions.json / enrollments.json
              (each either {id: doc} or a list of docs, optionally with an "id" field)
"""
import json
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

import db

COLLECTIONS = ["users", "courses", "sessions", "enrollments"]  # dependency order
DATE_FIELDS = {"dob", "dateStart", "dateEnd", "createdAt"}
REQUIRED = {
    "users": ["firstName", "lastName", "email", "role"],
    "courses": ["courseCode", "courseTitle"],
    "sessions": ["courseId", "trainerId", "dateStart", "dateEnd", "status"],
    "enrollments": ["sessionId", "userId", "paymentStatus", "attendanceStatus"],
}
ENUMS = {
    "users": {"role": ["ADMIN", "TRAINER", "USER"], "gender": ["MALE", "FEMALE"]},
    "sessions": {"status": ["OPEN", "CLOSED", "CANCELLED"]},
    "enrollments": {"paymentStatus": ["PAID", "UNPAID"],
                    "attendanceStatus": ["ATTENDED", "PARTIALLY ATTENDED", "UNATTENDED"]},
}
SAMPLE_PATH = Path(__file__).parent / "sample_data" / "sample_data.json"


# ------------------------------------------------------------- pure logic
def convert_dates(obj):
    """ISO strings in known date fields -> datetime (stored as Firestore timestamps)."""
    if isinstance(obj, dict):
        return {k: (datetime.fromisoformat(v) if k in DATE_FIELDS and isinstance(v, str) else convert_dates(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [convert_dates(x) for x in obj]
    return obj


def _normalize(payload):
    """{id: doc} or [doc, ...] -> {id: doc}"""
    if isinstance(payload, list):
        out = {}
        for d in payload:
            if not isinstance(d, dict):
                raise ValueError("list items must be objects")
            d = dict(d)
            out[str(d.pop("id", None) or uuid.uuid4().hex[:20])] = d
        return out
    if isinstance(payload, dict) and all(isinstance(v, dict) for v in payload.values()):
        return {str(k): v for k, v in payload.items()}
    raise ValueError("expected {id: document} or a list of documents")


def parse_uploads(files):
    """files: [(filename, bytes)] -> (data, problems)"""
    data = {c: {} for c in COLLECTIONS}
    problems = []
    for name, raw in files:
        try:
            obj = json.loads(raw.decode("utf-8-sig"))
        except Exception as e:
            problems.append(f"{name}: invalid JSON ({e})")
            continue
        try:
            if isinstance(obj, dict) and obj and set(obj) <= set(COLLECTIONS):
                for col, payload in obj.items():
                    data[col].update(_normalize(payload))
            else:
                col = next((c for c in COLLECTIONS if c in Path(name).stem.lower()), None)
                if not col:
                    problems.append(f"{name}: cannot tell which collection this is "
                                    f"(name it users/courses/sessions/enrollments.json or use a combined file)")
                    continue
                data[col].update(_normalize(obj))
        except ValueError as e:
            problems.append(f"{name}: {e}")
    return data, problems


def fix_enrolled_counts(data):
    if data["sessions"] and data["enrollments"]:
        n = Counter(e.get("sessionId") for e in data["enrollments"].values())
        for sid, s in data["sessions"].items():
            s["enrolledCount"] = n.get(sid, 0)


def validate(data, existing_ids):
    """Return (errors, warnings). existing_ids: {collection: set(ids already in Firestore)}."""
    errors, warns = [], []
    ids = {c: set(data[c]) | existing_ids.get(c, set()) for c in COLLECTIONS}
    for col in COLLECTIONS:
        for did, d in data[col].items():
            where = f"{col}/{did}"
            for f in REQUIRED[col]:
                if d.get(f) in (None, ""):
                    errors.append(f"{where}: missing required field '{f}'")
            for f, allowed in ENUMS.get(col, {}).items():
                if d.get(f) is not None and d[f] not in allowed:
                    errors.append(f"{where}: {f}='{d[f]}' not in {allowed}")
            for f in DATE_FIELDS:
                v = d.get(f)
                if isinstance(v, str):
                    try:
                        datetime.fromisoformat(v)
                    except ValueError:
                        errors.append(f"{where}: {f}='{v}' is not an ISO date (YYYY-MM-DD)")
    # uniqueness
    for col, field in [("users", "email"), ("courses", "courseCode")]:
        dup = [k for k, v in Counter(d.get(field) for d in data[col].values()).items() if k and v > 1]
        errors += [f"{col}: duplicate {field} '{k}'" for k in dup]
    # references
    for sid, s in data["sessions"].items():
        if s.get("courseId") and s["courseId"] not in ids["courses"]:
            errors.append(f"sessions/{sid}: courseId '{s['courseId']}' not found")
        if s.get("trainerId") and s["trainerId"] not in ids["users"]:
            errors.append(f"sessions/{sid}: trainerId '{s['trainerId']}' not found")
        if s.get("trainerId") in data["users"] and data["users"][s["trainerId"]].get("role") != "TRAINER":
            warns.append(f"sessions/{sid}: trainer '{s['trainerId']}' does not have role TRAINER")
    for eid, e in data["enrollments"].items():
        if e.get("sessionId") and e["sessionId"] not in ids["sessions"]:
            errors.append(f"enrollments/{eid}: sessionId '{e['sessionId']}' not found")
        if e.get("userId") and e["userId"] not in ids["users"]:
            errors.append(f"enrollments/{eid}: userId '{e['userId']}' not found")
        if e.get("sessionId") and e.get("userId") and eid != f"{e['sessionId']}_{e['userId']}":
            warns.append(f"enrollments/{eid}: id is not '<sessionId>_<userId>' – the app's duplicate "
                         "check will not recognise it")
    # seat counters
    if data["sessions"] and data["enrollments"]:
        n = Counter(e.get("sessionId") for e in data["enrollments"].values())
        for sid, s in data["sessions"].items():
            if s.get("enrolledCount") != n.get(sid, 0):
                warns.append(f"sessions/{sid}: enrolledCount={s.get('enrolledCount')} but "
                             f"{n.get(sid, 0)} enrollments found")
            mx = (s.get("capacity") or {}).get("maxPax")
            if mx and n.get(sid, 0) > mx:
                warns.append(f"sessions/{sid}: {n[sid]} enrollments exceed maxPax {mx}")
    return errors, warns


# ---------------------------------------------------------------- firestore
def existing_ids():
    c = db.db()
    return {col: {s.id for s in c.collection(col).select([]).stream()} for col in COLLECTIONS}


def delete_all(col):
    c, n = db.db(), 0
    refs = [s.reference for s in c.collection(col).select([]).stream()]
    for i in range(0, len(refs), 400):
        b = c.batch()
        for r in refs[i:i + 400]:
            b.delete(r)
        b.commit()
        n += len(refs[i:i + 400])
    return n


def run_import(data, mode, clear, progress):
    c = db.db()
    report = []
    if clear:
        for col in reversed(COLLECTIONS):
            report.append((col, f"deleted {delete_all(col)} existing"))
    total = sum(len(v) for v in data.values()) or 1
    done = 0
    for col in COLLECTIONS:
        skip = set()
        if mode == "Skip existing IDs" and not clear:
            skip = {s.id for s in c.collection(col).select([]).stream()}
        docs = [(k, v) for k, v in data[col].items() if k not in skip]
        for i in range(0, len(docs), 400):
            b = c.batch()
            for k, v in docs[i:i + 400]:
                b.set(c.collection(col).document(k), convert_dates(v))
            b.commit()
            done += len(docs[i:i + 400])
            progress.progress(min(done / total, 1.0), text=f"Importing {col}…")
        report.append((col, f"imported {len(docs)}" + (f", skipped {len(skip & set(data[col]))}" if skip else "")))
    return report


# ----------------------------------------------------------------------- UI
def flat(col, docs):
    return pd.DataFrame([{"id": k, **v} for k, v in docs.items()]) if docs else pd.DataFrame()


def main():
    st.set_page_config(page_title="Import Sample Data", page_icon="📥", layout="wide")
    st.title("📥 Import JSON data into Firestore")

    try:
        db.db()
    except Exception as e:
        st.error(f"Could not connect to Firebase.\n\n{e}")
        st.stop()
    st.caption(f"Connected to Firebase project **{db.db().project}**")

    st.subheader("1. Choose data")
    src = st.radio("Source", ["Bundled sample data", "Upload JSON file(s)"], horizontal=True)
    files = []
    if src == "Bundled sample data":
        if SAMPLE_PATH.exists():
            files = [(SAMPLE_PATH.name, SAMPLE_PATH.read_bytes())]
            st.info(f"Using `{SAMPLE_PATH.relative_to(Path(__file__).parent)}` "
                    "(20 users, 3 trainers, 1 admin, 10 courses, 5 sessions, 20 enrollments).")
        else:
            st.warning("sample_data/sample_data.json not found next to this app. Upload a file instead.")
    else:
        up = st.file_uploader("Combined file, or users/courses/sessions/enrollments .json",
                              type="json", accept_multiple_files=True)
        files = [(f.name, f.getvalue()) for f in up or []]

    if not files:
        st.stop()
    data, problems = parse_uploads(files)
    for p in problems:
        st.error(p)
    if not any(data.values()):
        st.stop()

    st.subheader("2. Options")
    c1, c2 = st.columns(2)
    mode = c1.radio("If a document ID already exists", ["Overwrite", "Skip existing IDs"], horizontal=True)
    fix = c2.checkbox("Recalculate sessions' enrolledCount from the enrollments", value=True,
                      help="Keeps the seat counter consistent with the imported enrollments.")
    clear = c1.checkbox("⚠️ Delete ALL existing documents in users, courses, sessions and enrollments first")
    confirm_ok = True
    if clear:
        confirm_ok = c2.text_input("Type DELETE to confirm") == "DELETE"
    if fix:
        fix_enrolled_counts(data)

    st.subheader("3. Preview & validation")
    try:
        ex = {c: set() for c in COLLECTIONS} if clear else existing_ids()
    except Exception as e:
        st.error(f"Could not read Firestore (is Cloud Firestore created for this project?)\n\n{e}")
        st.stop()
    errors, warns = validate(data, ex)

    cols = st.columns(4)
    for col, box in zip(COLLECTIONS, cols):
        box.metric(col, len(data[col]), help=f"{len(ex[col])} currently in Firestore" if not clear else None)
    tabs = st.tabs([c.capitalize() for c in COLLECTIONS])
    for col, tab in zip(COLLECTIONS, tabs):
        with tab:
            st.dataframe(flat(col, data[col]), use_container_width=True, hide_index=True)

    if errors:
        st.error(f"{len(errors)} error(s) – fix the JSON before importing")
        with st.expander("Errors", expanded=True):
            st.write("\n".join(f"- {e}" for e in errors[:100]))
    if warns:
        with st.expander(f"⚠️ {len(warns)} warning(s)"):
            st.write("\n".join(f"- {w}" for w in warns[:100]))
    if not errors:
        st.success("Validation passed.")

    st.subheader("4. Import")
    if st.button("🚀 Import into Firestore", type="primary", disabled=bool(errors) or not confirm_ok):
        bar = st.progress(0.0, text="Starting…")
        try:
            report = run_import(data, mode, clear, bar)
        except Exception as e:
            st.error(f"Import failed: {e}")
        else:
            bar.progress(1.0, text="Done")
            st.success("Import complete.")
            st.table(pd.DataFrame(report, columns=["collection", "result"]))
            st.caption("Imported users have no Firebase Auth login (passwords are never stored in Firestore).")


if __name__ == "__main__":
    main()