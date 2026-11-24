# Course Management System – Streamlit + Firebase (Firestore)

## 1. Relational → NoSQL design

5 relational tables + 2 child tables (`address`, `contact`) become **4 Firestore collections**.

| MySQL table | Firestore | Decision & why |
|---|---|---|
| `users` | `users/{uid}` | `password` **removed** → handled by Firebase Authentication (doc id = Auth uid when a login is created) |
| `address` (1:N user) | `users/{uid}.addresses[]` | Embedded array – always read with the user, never queried alone, small & bounded |
| `contact` (1:N user) | `users/{uid}.contacts[]` | Same as above |
| `courses` | `courses/{courseId}` | Stays top-level (referenced by many sessions) |
| `sessions` | `sessions/{sessionId}` | Location columns → `location{}` map; min/max pax → `capacity{}`; age/gender → `eligibility{}`. Denormalised `courseCode`, `courseTitle`, `trainerName`; counter `enrolledCount` |
| `enrollments` (M:N user↔session) | `enrollments/{sessionId_userId}` | Top-level so it can be queried by user *or* session. Deterministic id **prevents duplicate enrolments**. Denormalised `userName`, `userEmail`, `courseTitle`, `dateStart` |

### Example documents
```jsonc
// users/abc123
{ "firstName":"Aisha","lastName":"Rahman","gender":"FEMALE","dob":"1995-04-02","email":"aisha@x.com",
  "role":"TRAINER","userPhotoURL":null,"createdAt":"…",
  "addresses":[{"type":"HOME","unit":"12-3","street1":"Jln Ria","postalCode":"40000","city":"Shah Alam",
                "state":"Selangor","country":"Malaysia","permanent":"YES"}],
  "contacts":[{"contactType":"MOBILE","value":"+60123456789"}] }

// sessions/s1
{ "courseId":"c1","courseCode":"PY101","courseTitle":"Python Basics","trainerId":"abc123","trainerName":"Aisha Rahman",
  "dateStart":"2026-12-01","dateEnd":"2026-12-03",
  "location":{"name":"HQ Lab","city":"Shah Alam","state":"Selangor","country":"Malaysia","postalCode":"40000"},
  "capacity":{"minPax":5,"maxPax":20},
  "eligibility":{"minAge":18,"maxAge":60,"genderAllowed":"BOTH"},
  "status":"OPEN","pricePerPax":450.0,"enrolledCount":7 }

// enrollments/s1_u9
{ "sessionId":"s1","userId":"u9","userName":"Lee Wei","userEmail":"lee@x.com","courseCode":"PY101",
  "courseTitle":"Python Basics","dateStart":"2026-12-01",
  "paymentStatus":"PAID","attendanceStatus":"UNATTENDED","paymentReceiptUploadURL":"https://…" }
```

### Replacing relational features
| Relational feature | NoSQL replacement (implemented in `db.py`) |
|---|---|
| JOINs | Denormalised copies, kept in sync on update (user/course/session edits propagate via batched writes) |
| FK integrity / `ON DELETE` | App-level: course delete blocked if it has sessions; trainer delete blocked if they have sessions; deleting a session/user cascades to enrollments |
| `UNIQUE(email)` | Query check before write (+ Firebase Auth enforces uniqueness for logins) |
| Seat limit / capacity | **Firestore transaction**: checks status, eligibility (age/gender), `maxPax`, duplicates, then writes enrollment and `Increment(1)` on `enrolledCount` atomically |
| `DECIMAL(8,2)` | `float` rounded to 2 dp (store integer cents if you need exact money maths) |
| `DATE` | Firestore timestamp at 00:00 (converted back in the UI) |
| `AUTO_INCREMENT` | Firestore auto-ids (legacy ids kept as `legacyId` by the migration script) |

## 2. Setup
```bash
pip install -r requirements.txt
```
1. Firebase Console → create project → **Build → Firestore Database** (production mode).
2. (Optional, for user logins) **Build → Authentication → enable Email/Password**.
3. Project settings → Service accounts → **Generate new private key**.
4. Either save it as `serviceAccountKey.json` beside `app.py`, **or** copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and fill it in.
5. Run: `streamlit run app.py`

Never commit the key or `secrets.toml`. For Streamlit Community Cloud, paste the TOML into *App settings → Secrets*.

### Migrate existing MySQL data (optional)
```bash
python migrate_mysql_to_firestore.py --host localhost --user root --password *** --database coursemgmt
```

## 3. App features
- **Dashboard** – counts and charts
- **Users / Courses / Sessions / Enrollments** – each with *List* (search/filter), *Create*, *Edit / Delete* tabs
- Users: embedded address & contact editors; optional Firebase Auth login creation
- Enrollments: capacity, status, age and gender rules enforced transactionally

## 4. Notes / next steps
- The app uses the Admin SDK (bypasses security rules), so it must run server-side only. Add an admin login (e.g. Firebase Auth + `role == "ADMIN"`) before exposing it publicly.
- Firestore rules for any future client-side access: deny all by default (`allow read, write: if false;`).
- Composite indexes are avoided by sorting in Python; for large datasets add server-side `order_by` + indexes and pagination.
- Receipt/photo fields store URLs; add `firebase_admin.storage` if you want in-app file upload.
