"""One-off migration: MySQL (relational) -> Firestore (document model).

Usage:  python migrate_mysql_to_firestore.py --host localhost --user root --password ... --database coursemgmt
Needs serviceAccountKey.json in the working directory.
Passwords are intentionally NOT migrated (use Firebase Auth / password reset).
Legacy integer IDs are kept as (string) document IDs.
"""
import argparse
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal

import firebase_admin
import mysql.connector
from firebase_admin import credentials, firestore

p = argparse.ArgumentParser()
for a, d in [("host", "localhost"), ("user", "root"), ("password", ""), ("database", "coursemgmt")]:
    p.add_argument(f"--{a}", default=d)
args = p.parse_args()

firebase_admin.initialize_app(credentials.Certificate("serviceAccountKey.json"))
fs = firestore.client()
cur = mysql.connector.connect(host=args.host, user=args.user, password=args.password,
                              database=args.database).cursor(dictionary=True)


def q(sql):
    cur.execute(sql)
    return cur.fetchall()


def clean(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, date) and not isinstance(v, datetime):
        return datetime.combine(v, datetime.min.time())
    return v


def row(r, drop=()):
    return {k: clean(v) for k, v in r.items() if k not in drop}


class Writer:
    def __init__(self):
        self.b, self.n = fs.batch(), 0

    def set(self, col, doc_id, data):
        self.b.set(fs.collection(col).document(str(doc_id)), data)
        self.n += 1
        if self.n % 400 == 0:
            self.flush()

    def flush(self):
        self.b.commit()
        self.b = fs.batch()


w = Writer()

# users  (+ embedded addresses & contacts)
addr, cont = defaultdict(list), defaultdict(list)
for a in q("SELECT * FROM address ORDER BY addressid"):
    addr[a["userid"]].append(row(a, ("addressid", "userid", "createdAt")))
for c in q("SELECT * FROM contact ORDER BY contactid"):
    cont[c["userid"]].append(row(c, ("contactid", "userid", "createdAt")))
users = {u["userid"]: u for u in q("SELECT * FROM users")}
for uid, u in users.items():
    d = row(u, ("userid", "password"))
    d.update(addresses=addr[uid], contacts=cont[uid], legacyId=uid)
    w.set("users", uid, d)

# courses
courses = {c["courseid"]: c for c in q("SELECT * FROM courses")}
for cid, c in courses.items():
    w.set("courses", cid, {**row(c, ("courseid",)), "legacyId": cid})

# enrollments per session (for denormalised counter)
enr_rows = q("SELECT * FROM enrollments")
count = defaultdict(int)
for e in enr_rows:
    count[e["sessionid"]] += 1

# sessions
sessions = {s["sessionid"]: s for s in q("SELECT * FROM sessions")}
for sid, s in sessions.items():
    c, t = courses.get(s["courseid"], {}), users.get(s["trainerid"], {})
    w.set("sessions", sid, {
        "trainerId": str(s["trainerid"]), "courseId": str(s["courseid"]),
        "trainerName": f"{t.get('firstName', '')} {t.get('lastName', '')}".strip(),
        "courseCode": c.get("courseCode"), "courseTitle": c.get("courseTitle"),
        "dateStart": clean(s["dateStart"]), "dateEnd": clean(s["dateEnd"]),
        "location": {"name": s["locationName"], "unit": s["locationAddressUnit"],
                     "street1": s["locationStreet1"], "street2": s["locationStreet2"],
                     "postalCode": s["locationPostalCode"], "city": s["locationCity"],
                     "state": s["locationState"], "country": s["locationCountry"]},
        "capacity": {"minPax": s["minPax"], "maxPax": s["maxPax"]},
        "eligibility": {"minAge": s["minAge"], "maxAge": s["maxAge"], "genderAllowed": s["genderAllowed"]},
        "status": s["status"], "pricePerPax": clean(s["pricePerPax"]),
        "enrolledCount": count[sid], "createdAt": clean(s["createdAt"]), "legacyId": sid,
    })

# enrollments (deterministic id = sessionId_userId)
for e in enr_rows:
    s, u = sessions.get(e["sessionid"], {}), users.get(e["userid"], {})
    c = courses.get(s.get("courseid"), {})
    w.set("enrollments", f"{e['sessionid']}_{e['userid']}", {
        "sessionId": str(e["sessionid"]), "userId": str(e["userid"]),
        "userName": f"{u.get('firstName', '')} {u.get('lastName', '')}".strip(), "userEmail": u.get("email"),
        "courseCode": c.get("courseCode"), "courseTitle": c.get("courseTitle"),
        "dateStart": clean(s.get("dateStart")),
        "paymentStatus": e["paymentStatus"], "attendanceStatus": e["attendanceStatus"],
        "paymentReceiptUploadURL": e["paymentReceiptUploadURL"], "createdAt": clean(e["createdAt"]),
        "legacyId": e["enrolid"],
    })

w.flush()
print("Migration complete.")
