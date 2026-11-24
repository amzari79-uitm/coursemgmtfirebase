"""Course Management System – Streamlit + Firebase Firestore CRUD app."""
from datetime import date, timedelta

import pandas as pd
import streamlit as st

import db

st.set_page_config(page_title="Course Management", page_icon="🎓", layout="wide")

ROLES = ["USER", "TRAINER", "ADMIN"]
GENDERS = ["MALE", "FEMALE"]
ADDR_TYPES = ["HOME", "OFFICE", "OTHER"]
CONTACT_TYPES = ["MOBILE", "OFFICE", "HOME", "FACEBOOK", "IG", "WEBSITE", "TIKTOK", "YOUTUBE", "OTHER"]
SESSION_STATUS = ["OPEN", "CLOSED", "CANCELLED"]
PAYMENT = ["UNPAID", "PAID"]
ATTENDANCE = ["UNATTENDED", "PARTIALLY ATTENDED", "ATTENDED"]
ADDR_COLS = ["type", "unit", "street1", "street2", "postalCode", "city", "state", "country", "permanent"]
CONTACT_COLS = ["contactType", "value"]


# -------------------------------------------------------------------- helpers
def flash(msg, kind="success"):
    st.session_state["_flash"] = (kind, msg)
    st.rerun()


def show_flash():
    if "_flash" in st.session_state:
        kind, msg = st.session_state.pop("_flash")
        getattr(st, kind)(msg)


def idx(options, value, default=0):
    return options.index(value) if value in options else default


def records(df):
    """data_editor DataFrame -> list of clean dicts (drops blank rows / NaN)."""
    out = []
    for r in df.to_dict("records"):
        r = {k: (None if pd.isna(v) or v == "" else v) for k, v in r.items()}
        if any(v is not None for v in r.values()):
            out.append(r)
    return out


def guarded(fn, *args, ok="Saved."):
    try:
        fn(*args)
    except Exception as e:
        st.error(str(e))
    else:
        flash(ok)


def pick(label, docs, fmt, key):
    if not docs:
        st.info("No records yet.")
        return None
    ids = [d["id"] for d in docs]
    by_id = {d["id"]: d for d in docs}
    sel = st.selectbox(label, ids, format_func=lambda i: fmt(by_id[i]), key=key)
    return by_id[sel]


def delete_button(label, key, on_confirm):
    st.divider()
    sure = st.checkbox(f"I understand – permanently delete this {label}", key=f"{key}_sure")
    if st.button(f"🗑️ Delete {label}", key=f"{key}_del", disabled=not sure, type="primary"):
        guarded(on_confirm, ok=f"{label.capitalize()} deleted.")


# ---------------------------------------------------------------------- users
def user_form(key, u=None):
    new = u is None
    u = u or {}
    uid = u.get("id", "new")
    with st.form(key):
        c1, c2 = st.columns(2)
        first = c1.text_input("First name", u.get("firstName", ""))
        last = c2.text_input("Last name", u.get("lastName", ""))
        gender = c1.selectbox("Gender", GENDERS, index=idx(GENDERS, u.get("gender")))
        dob = c2.date_input("Date of birth", db.to_date(u.get("dob")) or date(2000, 1, 1),
                            min_value=date(1900, 1, 1), max_value=date.today())
        email = c1.text_input("Email", u.get("email", ""))
        role = c2.selectbox("Role", ROLES, index=idx(ROLES, u.get("role")))
        photo = c1.text_input("Photo URL", u.get("userPhotoURL", "") or "")
        pw = c2.text_input("Password (creates a Firebase Auth login; optional)", type="password") if new else None

        st.markdown("**Addresses**")
        addr = st.data_editor(
            pd.DataFrame(u.get("addresses", []), columns=ADDR_COLS), num_rows="dynamic",
            use_container_width=True, key=f"{key}_addr_{uid}",
            column_config={
                "type": st.column_config.SelectboxColumn(options=ADDR_TYPES),
                "permanent": st.column_config.SelectboxColumn(options=["YES", "NO"]),
                "postalCode": st.column_config.TextColumn(max_chars=6),
            })
        st.markdown("**Contacts**")
        cont = st.data_editor(
            pd.DataFrame(u.get("contacts", []), columns=CONTACT_COLS), num_rows="dynamic",
            use_container_width=True, key=f"{key}_cont_{uid}",
            column_config={"contactType": st.column_config.SelectboxColumn(options=CONTACT_TYPES)})
        ok = st.form_submit_button("Save", type="primary")
    if ok:
        if not (first and last and email):
            st.error("First name, last name and email are required.")
            return None
        return {"firstName": first, "lastName": last, "gender": gender, "dob": dob, "email": email,
                "role": role, "userPhotoURL": photo or None,
                "addresses": records(addr), "contacts": records(cont)}, pw
    return None


def page_users():
    st.header("👤 Users")
    show_flash()
    tab_l, tab_c, tab_e = st.tabs(["List", "Create", "Edit / Delete"])
    users = db.list_docs(db.USERS, sort_by="firstName")

    with tab_l:
        q = st.text_input("Search name / email", key="u_q").lower()
        role = st.multiselect("Role", ROLES, key="u_role")
        rows = [{"id": u["id"], "name": db.full_name(u), "email": u.get("email"), "role": u.get("role"),
                 "gender": u.get("gender"), "dob": db.to_date(u.get("dob")),
                 "#addresses": len(u.get("addresses", [])), "#contacts": len(u.get("contacts", []))}
                for u in users
                if (not q or q in db.full_name(u).lower() or q in (u.get("email") or "").lower())
                and (not role or u.get("role") in role)]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.caption(f"{len(rows)} user(s)")

    with tab_c:
        res = user_form("user_create")
        if res:
            data, pw = res
            guarded(db.create_user, data, pw or None, ok="User created.")

    with tab_e:
        u = pick("Select user", users, lambda d: f"{db.full_name(d)} <{d.get('email')}>", "u_pick")
        if u:
            res = user_form("user_edit", u)
            if res:
                guarded(db.update_user, u["id"], res[0], ok="User updated.")
            delete_button("user", "u", lambda: db.delete_user(u["id"]))


# -------------------------------------------------------------------- courses
def course_form(key, c=None):
    c = c or {}
    with st.form(key):
        code = st.text_input("Course code (max 8 chars)", c.get("courseCode", ""), max_chars=8)
        title = st.text_input("Title", c.get("courseTitle", ""), max_chars=300)
        desc = st.text_area("Description", c.get("courseDescription", "") or "")
        dur = st.number_input("Duration (days)", min_value=0, step=1, value=int(c.get("courseDuration") or 1))
        ok = st.form_submit_button("Save", type="primary")
    if ok:
        if not (code and title):
            st.error("Code and title are required.")
            return None
        return {"courseCode": code.strip().upper(), "courseTitle": title.strip(),
                "courseDescription": desc, "courseDuration": int(dur)}
    return None


def page_courses():
    st.header("📚 Courses")
    show_flash()
    tab_l, tab_c, tab_e = st.tabs(["List", "Create", "Edit / Delete"])
    courses = db.list_docs(db.COURSES, sort_by="courseCode")

    with tab_l:
        q = st.text_input("Search code / title", key="c_q").lower()
        rows = [{"id": c["id"], "code": c["courseCode"], "title": c["courseTitle"],
                 "duration": c.get("courseDuration"), "description": c.get("courseDescription")}
                for c in courses if not q or q in c["courseCode"].lower() or q in c["courseTitle"].lower()]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    with tab_c:
        data = course_form("course_create")
        if data:
            guarded(db.create_course, data, ok="Course created.")

    with tab_e:
        c = pick("Select course", courses, lambda d: f"{d['courseCode']} – {d['courseTitle']}", "c_pick")
        if c:
            data = course_form("course_edit", c)
            if data:
                guarded(db.update_course, c["id"], data, ok="Course updated.")
            delete_button("course", "c", lambda: db.delete_course(c["id"]))


# ------------------------------------------------------------------- sessions
def session_form(key, courses, trainers, s=None):
    s = s or {}
    loc, cap, elig = s.get("location", {}), s.get("capacity", {}), s.get("eligibility", {})
    if not courses or not trainers:
        st.warning("Create at least one course and one user with role TRAINER first.")
        return None
    cid = [c["id"] for c in courses]
    tid = [t["id"] for t in trainers]
    cmap = {c["id"]: f"{c['courseCode']} – {c['courseTitle']}" for c in courses}
    tmap = {t["id"]: db.full_name(t) for t in trainers}
    with st.form(key):
        c1, c2 = st.columns(2)
        course = c1.selectbox("Course", cid, index=idx(cid, s.get("courseId")), format_func=cmap.get)
        trainer = c2.selectbox("Trainer", tid, index=idx(tid, s.get("trainerId")), format_func=tmap.get)
        d1 = c1.date_input("Start date", db.to_date(s.get("dateStart")) or date.today())
        d2 = c2.date_input("End date", db.to_date(s.get("dateEnd")) or date.today() + timedelta(days=1))
        st.markdown("**Location**")
        l1, l2 = st.columns(2)
        name = l1.text_input("Venue name", loc.get("name", "") or "")
        unit = l2.text_input("Unit", loc.get("unit", "") or "")
        st1 = l1.text_input("Street 1", loc.get("street1", "") or "")
        st2 = l2.text_input("Street 2", loc.get("street2", "") or "")
        pc = l1.text_input("Postal code", loc.get("postalCode", "") or "", max_chars=6)
        city = l2.text_input("City", loc.get("city", "") or "")
        state = l1.text_input("State", loc.get("state", "") or "")
        country = l2.text_input("Country", loc.get("country", "") or "")
        st.markdown("**Capacity, eligibility & price**")
        k1, k2, k3, k4 = st.columns(4)
        min_pax = k1.number_input("Min pax", 0, value=int(cap.get("minPax") or 1))
        max_pax = k2.number_input("Max pax", 0, value=int(cap.get("maxPax") or 20))
        min_age = k3.number_input("Min age", 0, value=int(elig.get("minAge") or 0))
        max_age = k4.number_input("Max age", 0, value=int(elig.get("maxAge") or 99))
        g = k1.selectbox("Gender allowed", ["BOTH", "MALE", "FEMALE"],
                         index=idx(["BOTH", "MALE", "FEMALE"], elig.get("genderAllowed")))
        status = k2.selectbox("Status", SESSION_STATUS, index=idx(SESSION_STATUS, s.get("status")))
        price = k3.number_input("Price per pax", 0.0, step=10.0, format="%.2f", value=float(s.get("pricePerPax") or 0))
        ok = st.form_submit_button("Save", type="primary")
    if ok:
        if d2 < d1:
            st.error("End date must not be before start date.")
        elif min_pax > max_pax or min_age > max_age:
            st.error("Min values cannot exceed max values.")
        else:
            return {"courseId": course, "trainerId": trainer, "dateStart": d1, "dateEnd": d2,
                    "location": {"name": name, "unit": unit, "street1": st1, "street2": st2,
                                 "postalCode": pc, "city": city, "state": state, "country": country},
                    "capacity": {"minPax": int(min_pax), "maxPax": int(max_pax)},
                    "eligibility": {"minAge": int(min_age), "maxAge": int(max_age), "genderAllowed": g},
                    "status": status, "pricePerPax": round(float(price), 2)}
    return None


def page_sessions():
    st.header("🗓️ Sessions")
    show_flash()
    tab_l, tab_c, tab_e = st.tabs(["List", "Create", "Edit / Delete"])
    sessions = db.list_docs(db.SESSIONS, sort_by="dateStart", reverse=True)
    courses = db.list_docs(db.COURSES, sort_by="courseCode")
    trainers = db.list_docs(db.USERS, [("role", "==", "TRAINER")], sort_by="firstName")

    with tab_l:
        status = st.multiselect("Status", SESSION_STATUS, key="s_status")
        rows = [{"id": s["id"], "course": s["courseTitle"], "trainer": s["trainerName"],
                 "start": db.to_date(s["dateStart"]), "end": db.to_date(s["dateEnd"]),
                 "venue": s.get("location", {}).get("name"), "city": s.get("location", {}).get("city"),
                 "enrolled": f"{s.get('enrolledCount', 0)}/{s.get('capacity', {}).get('maxPax')}",
                 "price": s.get("pricePerPax"), "status": s.get("status")}
                for s in sessions if not status or s.get("status") in status]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    with tab_c:
        data = session_form("sess_create", courses, trainers)
        if data:
            guarded(db.create_session, data, ok="Session created.")

    with tab_e:
        s = pick("Select session", sessions,
                 lambda d: f"{d['courseCode']} | {db.to_date(d['dateStart'])} | {d['trainerName']}", "s_pick")
        if s:
            data = session_form("sess_edit", courses, trainers, s)
            if data:
                guarded(db.update_session, s["id"], data, ok="Session updated.")
            st.caption("Deleting a session also deletes its enrollments.")
            delete_button("session", "s", lambda: db.delete_session(s["id"]))


# ---------------------------------------------------------------- enrollments
def page_enrollments():
    st.header("📝 Enrollments")
    show_flash()
    tab_l, tab_c, tab_e = st.tabs(["List", "Enrol user", "Edit / Delete"])
    sessions = db.list_docs(db.SESSIONS, sort_by="dateStart", reverse=True)
    smap = {s["id"]: s for s in sessions}

    def slabel(s):
        return (f"{s['courseCode']} | {db.to_date(s['dateStart'])} | {s['status']} | "
                f"{s.get('enrolledCount', 0)}/{s.get('capacity', {}).get('maxPax')} seats")

    with tab_l:
        f1, f2 = st.columns(2)
        fs = f1.selectbox("Session", [None] + list(smap), format_func=lambda i: "All" if i is None else slabel(smap[i]))
        fp = f2.multiselect("Payment", PAYMENT)
        flt = [("sessionId", "==", fs)] if fs else None
        enr = db.list_docs(db.ENROLLMENTS, flt, sort_by="dateStart", reverse=True)
        rows = [{"id": e["id"], "user": e.get("userName"), "email": e.get("userEmail"),
                 "course": e.get("courseTitle"), "start": db.to_date(e.get("dateStart")),
                 "payment": e.get("paymentStatus"), "attendance": e.get("attendanceStatus"),
                 "receipt": e.get("paymentReceiptUploadURL")}
                for e in enr if not fp or e.get("paymentStatus") in fp]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True,
                     column_config={"receipt": st.column_config.LinkColumn()})

    with tab_c:
        users = db.list_docs(db.USERS, sort_by="firstName")
        if not sessions or not users:
            st.info("You need at least one session and one user.")
        else:
            umap = {u["id"]: u for u in users}
            with st.form("enr_create"):
                sid = st.selectbox("Session", list(smap), format_func=lambda i: slabel(smap[i]))
                uid = st.selectbox("User", list(umap), format_func=lambda i: f"{db.full_name(umap[i])} <{umap[i].get('email')}>")
                c1, c2 = st.columns(2)
                pay = c1.selectbox("Payment status", PAYMENT)
                att = c2.selectbox("Attendance status", ATTENDANCE)
                rcpt = st.text_input("Payment receipt URL")
                ok = st.form_submit_button("Enrol", type="primary")
            if ok:
                guarded(db.create_enrollment, sid, uid,
                        {"paymentStatus": pay, "attendanceStatus": att,
                         "paymentReceiptUploadURL": rcpt or None}, ok="User enrolled.")

    with tab_e:
        all_enr = db.list_docs(db.ENROLLMENTS, sort_by="dateStart", reverse=True)
        e = pick("Select enrollment", all_enr,
                 lambda d: f"{d.get('userName')} → {d.get('courseCode')} ({db.to_date(d.get('dateStart'))})", "e_pick")
        if e:
            with st.form("enr_edit"):
                c1, c2 = st.columns(2)
                pay = c1.selectbox("Payment status", PAYMENT, index=idx(PAYMENT, e.get("paymentStatus")))
                att = c2.selectbox("Attendance status", ATTENDANCE, index=idx(ATTENDANCE, e.get("attendanceStatus")))
                rcpt = st.text_input("Payment receipt URL", e.get("paymentReceiptUploadURL") or "")
                ok = st.form_submit_button("Save", type="primary")
            if ok:
                guarded(db.update_enrollment, e["id"],
                        {"paymentStatus": pay, "attendanceStatus": att,
                         "paymentReceiptUploadURL": rcpt or None}, ok="Enrollment updated.")
            delete_button("enrollment", "e", lambda: db.delete_enrollment(e["id"]))


# ------------------------------------------------------------------ dashboard
def page_dashboard():
    st.header("🎓 Dashboard")
    users = db.list_docs(db.USERS)
    sessions = db.list_docs(db.SESSIONS)
    enr = db.list_docs(db.ENROLLMENTS)
    c = st.columns(4)
    c[0].metric("Users", len(users))
    c[1].metric("Courses", len(db.list_docs(db.COURSES)))
    c[2].metric("Open sessions", sum(s.get("status") == "OPEN" for s in sessions))
    c[3].metric("Enrollments", len(enr))
    if enr:
        df = pd.DataFrame(enr)
        st.subheader("Enrollments by course")
        st.bar_chart(df.groupby("courseTitle").size())
        st.subheader("Payment status")
        st.bar_chart(df.groupby("paymentStatus").size())


# ----------------------------------------------------------------------- main
PAGES = {"Dashboard": page_dashboard, "Users": page_users, "Courses": page_courses,
         "Sessions": page_sessions, "Enrollments": page_enrollments}

st.sidebar.title("🎓 Course Management")
choice = st.sidebar.radio("Navigate", list(PAGES))
try:
    db.db()
except Exception as e:
    st.error("Could not connect to Firebase. Add credentials to `.streamlit/secrets.toml` "
             f"or place `serviceAccountKey.json` next to app.py.\n\n{e}")
    st.stop()
PAGES[choice]()
