# Semgrep rule test fixture (annotations: "ruleid" must match, "ok" must not).
# This file is test data only; it is never imported or executed.
import hashlib
import os
import pickle
import subprocess
import tempfile

import jwt
import requests
import yaml


def sql(cursor, user_id, name):
    # ruleid: python.sql-injection.string-built-query
    cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")
    # ruleid: python.sql-injection.string-built-query
    cursor.execute("SELECT * FROM users WHERE name = '%s'" % name)
    # ruleid: python.sql-injection.string-built-query
    cursor.execute("DELETE FROM t WHERE id = " + user_id)
    query = f"UPDATE users SET name = '{name}'"
    # ruleid: python.sql-injection.string-built-query
    cursor.execute(query)
    # ok: python.sql-injection.string-built-query
    cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
    # ruleid: python.sql-injection.string-built-query
    User.objects.raw(f"SELECT * FROM auth_user WHERE id = {user_id}")


def code(expr):
    # ruleid: python.code-injection.eval
    eval(expr)
    # ok: python.code-injection.eval
    eval("1 + 1")
    # ruleid: python.code-injection.eval
    exec(expr)


def shell(cmd, ref):
    # ruleid: python.command-injection.shell
    subprocess.run(cmd, shell=True)
    # ok: python.command-injection.shell
    subprocess.run(["git", "log", ref], check=True)
    # ok: python.command-injection.shell
    subprocess.run("ls -la", shell=True)
    # ruleid: python.command-injection.shell
    os.system("rm -rf " + ref)


def load(data):
    # ruleid: python.deserialization.unsafe-yaml
    yaml.load(data)
    # ok: python.deserialization.unsafe-yaml
    yaml.load(data, Loader=yaml.SafeLoader)
    # ok: python.deserialization.unsafe-yaml
    yaml.safe_load(data)
    # ruleid: python.deserialization.pickle
    pickle.loads(data)


def tls(url):
    # ruleid: python.tls.verify-disabled
    requests.get(url, verify=False)
    # ok: python.tls.verify-disabled
    requests.get(url, timeout=5)


def hashing(data):
    # ruleid: python.crypto.weak-hash
    hashlib.md5(data)
    # ok: python.crypto.weak-hash
    hashlib.md5(data, usedforsecurity=False)
    # ok: python.crypto.weak-hash
    hashlib.sha256(data)


# ruleid: python.secrets.hardcoded-credential
DB_PASSWORD = "s3cr3t-Pa55w0rd!"
# ok: python.secrets.hardcoded-credential
API_KEY = "changeme-please"
# ok: python.secrets.hardcoded-credential
password = ""
# ok: python.secrets.hardcoded-credential
API_KEY = os.environ["API_KEY"]


def flask_app(app):
    # ruleid: python.flask.debug-enabled
    app.run(debug=True)


def tokens(token, key):
    # ruleid: python.jwt.signature-not-verified
    jwt.decode(token, key, options={"verify_signature": False})
    # ok: python.jwt.signature-not-verified
    jwt.decode(token, key, algorithms=["RS256"])


def temp():
    # ruleid: python.tempfile.mktemp
    return tempfile.mktemp()


# ruleid: python.django.csrf-exempt
@csrf_exempt
def webhook(request):
    return None
