from pathlib import Path
import streamlit as st

st.set_page_config(page_title="Verify secrets.toml", layout="centered")
st.title("Streamlit Secrets Verifier")

# 1. Check file location
expected_path = Path.cwd() / ".streamlit" / "secrets.toml"
st.subheader("1. File Location")
st.code(str(expected_path))

if not expected_path.exists():
    st.error("`secrets.toml` was not found at the expected path.")
    st.stop()

st.success("`secrets.toml` found.")

# 2. Inspect top-level keys in st.secrets
st.subheader("2. Top-Level Sections & Keys")
top_keys = list(st.secrets.keys())
st.write("Top-level keys:", top_keys)

if not top_keys:
    st.warning("`st.secrets` is empty or could not be parsed.")
    st.stop()

# 3. Choose which section/dictionary to inspect
source_choice = st.selectbox(
    "Select the dictionary you pass to `credentials.Certificate(...)`:",
    options=["st.secrets (root)"] + [f'st.secrets["{k}"]' for k in top_keys],
)

if source_choice == "st.secrets (root)":
    cred_dict = dict(st.secrets)
else:
    selected_key = source_choice.split('"')[1]
    cred_dict = dict(st.secrets[selected_key])

# 4. Check required Firebase service account fields
required_fields = [
    "type",
    "project_id",
    "private_key_id",
    "private_key",
    "client_email",
    "client_id",
    "auth_uri",
    "token_uri",
]

st.subheader("3. Required Service Account Fields")
missing = [field for field in required_fields if not cred_dict.get(field)]

for field in required_fields:
    val = cred_dict.get(field)
    if field == "private_key":
        if isinstance(val, str) and "BEGIN PRIVATE KEY" in val and "END PRIVATE KEY" in val:
            st.success(f"`{field}`: present (looks like a valid PEM key)")
        elif isinstance(val, str) and val.strip():
            st.warning(f"`{field}`: present, but check `BEGIN/END PRIVATE KEY` and newlines")
        else:
            st.error(f"`{field}`: missing or empty")
    else:
        if val:
            st.success(f"`{field}`: `{val}`")
        else:
            st.error(f"`{field}`: missing or empty")

# 5. Optional live test with firebase_admin
st.subheader("4. Optional `firebase_admin` Test")
if st.button("Test Certificate Initialization"):
    if missing:
        st.error(f"Cannot initialize yet. Missing fields in selected section: {', '.join(missing)}")
    else:
        try:
            import firebase_admin
            from firebase_admin import credentials

            # Normalize escaped \n if private_key was pasted on one line
            if isinstance(cred_dict.get("private_key"), str):
                cred_dict["private_key"] = cred_dict["private_key"].replace("\\n", "\n")

            cert = credentials.Certificate(cred_dict)
            if not firebase_admin._apps:
                firebase_admin.initialize_app(cert)
            st.success("`credentials.Certificate(...)` succeeded!")
        except Exception as exc:
            st.error(f"Initialization failed: {exc}")