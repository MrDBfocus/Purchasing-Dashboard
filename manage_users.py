"""
Manage who can sign in to the CPJ Purchasing Dashboard.

Run these in the dashboard folder (the one that holds app.py):

    python manage_users.py add jsmith                 add a user (you will be asked for a password)
    python manage_users.py add jsmith --name "Jane Smith"
    python manage_users.py password jsmith            change a user's password
    python manage_users.py remove jsmith              remove a user
    python manage_users.py list                       show who can sign in
    python manage_users.py export-secrets             print the users in the format Streamlit Cloud 'Secrets' expects

Passwords are never stored as text. Only a scrambled version (PBKDF2-SHA256, 200,000 rounds, random salt) is saved in users.json.
"""
import getpass
import hashlib
import json
import os
import sys

USERS_FILE = os.environ.get("CPJ_USERS_FILE", "users.json")
ITERATIONS = 200_000
MIN_LENGTH = 8


def make_password_hash(password, iterations=ITERATIONS):
    """Same format the dashboard checks: pbkdf2_sha256$iterations$salt$hash"""
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${dk.hex()}"


def load():
    if not os.path.exists(USERS_FILE):
        return {}
    try:
        with open(USERS_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        sys.exit(f"Could not read {USERS_FILE}: {e}\nFix or delete the file, then try again.")


def save(users):
    tmp = USERS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(users, fh, indent=2)
    os.replace(tmp, USERS_FILE)                      # never leaves a half-written file behind
    try:
        os.chmod(USERS_FILE, 0o600)                  # only the owner can read it (ignored on Windows)
    except Exception:
        pass


def ask_password(username):
    for _ in range(3):
        p1 = getpass.getpass(f"New password for {username}: ")
        if len(p1) < MIN_LENGTH:
            print(f"  Please use at least {MIN_LENGTH} characters.")
            continue
        if p1.strip().lower() == username.strip().lower():
            print("  The password must not be the same as the user name.")
            continue
        if p1 != getpass.getpass("Type it again: "):
            print("  The two passwords did not match.")
            continue
        return p1
    sys.exit("No password was set.")


def key_of(name):
    return name.strip().lower()


def main(argv):
    if len(argv) < 2 or argv[1] in ("-h", "--help", "help"):
        print(__doc__)
        return
    cmd = argv[1].lower()
    users = load()
    if cmd == "list":
        if not users:
            print("No users yet. Add one with: python manage_users.py add <user name>")
        for k, v in sorted(users.items()):
            print(f"  {k:20s} {v.get('name', '')}")
        return
    if cmd == "export-secrets":
        print("[users]")
        for k, v in sorted(users.items()):
            print(f'{k} = "{v["hash"]}"')
        return
    if len(argv) < 3:
        sys.exit("Please give a user name, for example: python manage_users.py add jsmith")
    username = key_of(argv[2])
    if not username or not all(c.isalnum() or c in "._-@" for c in username):
        sys.exit("User names may use letters, numbers and . _ - @ only.")
    if cmd == "add":
        if username in users:
            sys.exit(f"'{username}' already exists. Use 'password {username}' to change the password.")
        display = username
        if "--name" in argv:
            i = argv.index("--name")
            display = " ".join(argv[i + 1:]).strip() or username
        users[username] = {"name": display, "hash": make_password_hash(ask_password(username))}
        save(users)
        print(f"Added {username}. They can sign in now.")
    elif cmd == "password":
        if username not in users:
            sys.exit(f"No user called '{username}'. Use 'list' to see who exists.")
        users[username]["hash"] = make_password_hash(ask_password(username))
        save(users)
        print(f"Password changed for {username}.")
    elif cmd == "remove":
        if username not in users:
            sys.exit(f"No user called '{username}'.")
        if input(f"Remove {username}? Type yes to confirm: ").strip().lower() != "yes":
            sys.exit("Nothing changed.")
        users.pop(username)
        save(users)
        print(f"Removed {username}.")
    else:
        sys.exit(f"Unknown command '{cmd}'. Run: python manage_users.py help")


if __name__ == "__main__":
    main(sys.argv)
