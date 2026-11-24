"""Firestore data-access layer for the Course Management System."""
from datetime import date, datetime

import firebase_admin
import streamlit as st
from firebase_admin import auth, credentials, firestore
from google.cloud.firestore_v1.base_query import FieldFilter

USERS, COURSES, SESSIONS, ENROLLMENTS = "users", "courses", "sessions", "enrollments"


# ----------------------------------------------------------------- connection
REQUIRED_KEYS = ["project_id", "private_key", "client_email"]


def _load_service_account():
    """Return the service-account dict from secrets.toml or serviceAccountKey.json."""
    import json
    import os

    info, source = None, None
    if "firebase" in st.secrets:
        info, source = dict(st.secrets["firebase"]), ".streamlit/secrets.toml [firebase]"
    elif os.path.exists("serviceAccountKey.json"):
        with open("serviceAccountKey.json") as f:
            info, source = json.load(f), "serviceAccountKey.json"
    if info is None:
        raise RuntimeError("No credentials found. Add a [firebase] section to .streamlit/secrets.toml "
                           "or place serviceAccountKey.json next to app.py.")
    info.setdefault("type", "service_account")
    info.setdefault("token_uri", "https://oauth2.googleapis.com/token")
    missing = [k for k in REQUIRED_KEYS if not info.get(k)]
    if missing:
        raise RuntimeError(f"Credentials loaded from {source} are missing: {', '.join(missing)}. "
                           f"Keys found: {', '.join(sorted(info))}. "
                           "Paste the values from the service-account JSON downloaded from "
                           "Firebase Console > Project settings > Service accounts.")
    if "PASTE_" in info["private_key"] or "xxxxx" in info["client_email"]:
        raise RuntimeError("secrets.toml still contains placeholder values. Replace them with the "
                           "values from your service-account JSON.")
    info["private_key"] = info["private_key"].replace("\\n", "\n")  # tolerate escaped newlines
    return info


@st.cache_resource
def db():
    if not firebase_admin._apps:
        firebase_admin.initialize_app(credentials.Certificate(_load_service_account()))
    return firestore.client()


# -------------------------------------------------------------------- helpers
def to_date(v):
    """Firestore timestamp/datetime -> date."""
    if v is None:
        return None
    return v.date() if isinstance(v, datetime) else v


def to_dt(v):
    """date -> datetime (Firestore has no pure DATE type)."""
    if isinstance(v, date) and not isinstance(v, datetime):
        return datetime.combine(v, datetime.min.time())
    return v


def _with_id(snap):
    d = snap.to_dict() or {}
    d["id"] = snap.id
    return d


def _query(col, filters=None):
    q = db().collection(col)
    for f, op, v in filters or []:
        q = q.where(filter=FieldFilter(f, op, v))
    return q


def list_docs(col, filters=None, sort_by=None, reverse=False):
    docs = [_with_id(s) for s in _query(col, filters).stream()]
    if sort_by:  # sort client-side: avoids composite-index requirements
        docs.sort(key=lambda d: (d.get(sort_by) is None, d.get(sort_by)), reverse=reverse)
    return docs


def get_doc(col, doc_id):
    snap = db().collection(col).document(doc_id).get()
    return _with_id(snap) if snap.exists else None


def _batch_update(refs, fields):
    refs = list(refs)
    for i in range(0, len(refs), 400):
        b = db().batch()
        for r in refs[i:i + 400]:
            b.update(r, fields)
        b.commit()


def _batch_delete(refs):
    refs = list(refs)
    for i in range(0, len(refs), 400):
        b = db().batch()
        for r in refs[i:i + 400]:
            b.delete(r)
        b.commit()


def full_name(u):
    return f"{u.get('firstName', '')} {u.get('lastName', '')}".strip()


# ---------------------------------------------------------------------- users
def email_taken(email, exclude_id=None):
    hits = list_docs(USERS, [("email", "==", email)])
    return any(h["id"] != exclude_id for h in hits)


def create_user(data, password=None):
    """Passwords are NOT stored in Firestore. If given, a Firebase Auth account is
    created and its uid is used as the document id."""
    if email_taken(data["email"]):
        raise ValueError("Email already exists.")
    data = {**data, "dob": to_dt(data.get("dob")), "createdAt": firestore.SERVER_TIMESTAMP}
    if password:
        rec = auth.create_user(email=data["email"], password=password, display_name=full_name(data))
        ref = db().collection(USERS).document(rec.uid)
    else:
        ref = db().collection(USERS).document()
    ref.set(data)
    return ref.id


def update_user(uid, data):
    if email_taken(data["email"], exclude_id=uid):
        raise ValueError("Email already exists.")
    data = {**data, "dob": to_dt(data.get("dob"))}
    db().collection(USERS).document(uid).update(data)
    # propagate denormalised copies
    name = full_name(data)
    _batch_update(
        [s.reference for s in _query(ENROLLMENTS, [("userId", "==", uid)]).stream()],
        {"userName": name, "userEmail": data["email"]},
    )
    _batch_update(
        [s.reference for s in _query(SESSIONS, [("trainerId", "==", uid)]).stream()],
        {"trainerName": name},
    )


def delete_user(uid):
    if list_docs(SESSIONS, [("trainerId", "==", uid)]):
        raise ValueError("This user is the trainer of existing sessions. Reassign or delete those sessions first.")
    for e in list_docs(ENROLLMENTS, [("userId", "==", uid)]):
        delete_enrollment(e["id"])
    db().collection(USERS).document(uid).delete()
    try:
        auth.delete_user(uid)
    except Exception:
        pass  # no matching Auth account


# -------------------------------------------------------------------- courses
def create_course(data):
    if list_docs(COURSES, [("courseCode", "==", data["courseCode"])]):
        raise ValueError("Course code already exists.")
    ref = db().collection(COURSES).document()
    ref.set({**data, "createdAt": firestore.SERVER_TIMESTAMP})
    return ref.id


def update_course(cid, data):
    db().collection(COURSES).document(cid).update(data)
    refs = [s.reference for s in _query(SESSIONS, [("courseId", "==", cid)]).stream()]
    _batch_update(refs, {"courseCode": data["courseCode"], "courseTitle": data["courseTitle"]})
    for sid in [r.id for r in refs]:
        _batch_update(
            [s.reference for s in _query(ENROLLMENTS, [("sessionId", "==", sid)]).stream()],
            {"courseCode": data["courseCode"], "courseTitle": data["courseTitle"]},
        )


def delete_course(cid):
    if list_docs(SESSIONS, [("courseId", "==", cid)]):
        raise ValueError("Course has sessions. Delete the sessions first.")
    db().collection(COURSES).document(cid).delete()


# ------------------------------------------------------------------- sessions
def _session_payload(data):
    course = get_doc(COURSES, data["courseId"])
    trainer = get_doc(USERS, data["trainerId"])
    if not course or not trainer:
        raise ValueError("Course or trainer not found.")
    return {
        **data,
        "dateStart": to_dt(data["dateStart"]),
        "dateEnd": to_dt(data["dateEnd"]),
        "courseCode": course["courseCode"],
        "courseTitle": course["courseTitle"],
        "trainerName": full_name(trainer),
    }


def create_session(data):
    ref = db().collection(SESSIONS).document()
    ref.set({**_session_payload(data), "enrolledCount": 0, "createdAt": firestore.SERVER_TIMESTAMP})
    return ref.id


def update_session(sid, data):
    payload = _session_payload(data)
    db().collection(SESSIONS).document(sid).update(payload)
    _batch_update(
        [s.reference for s in _query(ENROLLMENTS, [("sessionId", "==", sid)]).stream()],
        {"courseCode": payload["courseCode"], "courseTitle": payload["courseTitle"],
         "dateStart": payload["dateStart"]},
    )


def delete_session(sid):
    _batch_delete([s.reference for s in _query(ENROLLMENTS, [("sessionId", "==", sid)]).stream()])
    db().collection(SESSIONS).document(sid).delete()


# ---------------------------------------------------------------- enrollments
def check_eligibility(user, sess):
    errs = []
    if sess.get("status") != "OPEN":
        errs.append(f"Session is {sess.get('status')}.")
    elig = sess.get("eligibility", {})
    g = elig.get("genderAllowed", "BOTH")
    if g != "BOTH" and user.get("gender") != g:
        errs.append(f"Session is for {g} only.")
    dob, start = to_date(user.get("dob")), to_date(sess.get("dateStart"))
    if dob and start:
        age = start.year - dob.year - ((start.month, start.day) < (dob.month, dob.day))
        if elig.get("minAge") and age < elig["minAge"]:
            errs.append(f"Minimum age is {elig['minAge']} (user is {age}).")
        if elig.get("maxAge") and age > elig["maxAge"]:
            errs.append(f"Maximum age is {elig['maxAge']} (user is {age}).")
    return errs


@firestore.transactional
def _enrol_txn(txn, sess_ref, enr_ref, user, extra):
    s = sess_ref.get(transaction=txn)
    if not s.exists:
        raise ValueError("Session not found.")
    sd = s.to_dict()
    if enr_ref.get(transaction=txn).exists:
        raise ValueError("User is already enrolled in this session.")
    errs = check_eligibility(user, sd)
    max_pax = sd.get("capacity", {}).get("maxPax")
    if max_pax and sd.get("enrolledCount", 0) >= max_pax:
        errs.append("Session is full.")
    if errs:
        raise ValueError(" ".join(errs))
    txn.set(enr_ref, {
        "sessionId": s.id, "userId": user["id"],
        "userName": full_name(user), "userEmail": user.get("email"),
        "courseCode": sd["courseCode"], "courseTitle": sd["courseTitle"],
        "dateStart": sd["dateStart"], **extra,
        "createdAt": firestore.SERVER_TIMESTAMP,
    })
    txn.update(sess_ref, {"enrolledCount": firestore.Increment(1)})


def create_enrollment(session_id, user_id, extra):
    user = get_doc(USERS, user_id)
    if not user:
        raise ValueError("User not found.")
    c = db()
    # deterministic id prevents duplicate enrolments (same user + session)
    _enrol_txn(c.transaction(), c.collection(SESSIONS).document(session_id),
               c.collection(ENROLLMENTS).document(f"{session_id}_{user_id}"), user, extra)


def update_enrollment(enrol_id, data):
    db().collection(ENROLLMENTS).document(enrol_id).update(data)


@firestore.transactional
def _del_enr_txn(txn, enr_ref, sess_col):
    snap = enr_ref.get(transaction=txn)
    if not snap.exists:
        return
    sref = sess_col.document(snap.get("sessionId"))
    s = sref.get(transaction=txn)
    txn.delete(enr_ref)
    if s.exists:
        txn.update(sref, {"enrolledCount": firestore.Increment(-1)})


def delete_enrollment(enrol_id):
    c = db()
    _del_enr_txn(c.transaction(), c.collection(ENROLLMENTS).document(enrol_id), c.collection(SESSIONS))