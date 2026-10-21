# SPDX-License-Identifier: GPL-2.0-or-later
"""Front-end conformance suite: run with scripts/conformance <driver>."""

import json
import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..", "..", "src"), os.path.join(HERE, "..", "harness")]

import nmjson  # noqa: E402
import pkihelp  # noqa: E402
from fake_openvpn3 import fake_snapshot  # noqa: E402

STATIC_PROFILES = ["cert", "files", "userpass-inline", "userpass-agent", "static-challenge",
                   "connection-block", "comments"]
GENERATED_PROFILES = ["pkcs12", "encrypted-key"]
PROFILES = STATIC_PROFILES + GENERATED_PROFILES
FIXTURES_DIR = os.path.join(HERE, "..", "fixtures", "connections")
FIXTURES = sorted(f[:-5] for f in os.listdir(FIXTURES_DIR) if f.endswith(".json"))

# What the user types when asked.
ANSWERS = {"password": "s3cret", "cert-pass": "p12pass", "challenge-response": "123456"}


def pytest_addoption(parser):
    parser.addoption("--driver", required=True, help="front-end driver, see docs/frontends.md")


class Driver:
    def __init__(self, path, tmp):
        self.path, self.tmp, self.n = path, tmp, 0

    def _file(self, name):
        self.n += 1
        return str(self.tmp / f"{self.n}-{name}")

    def run(self, *args):
        p = subprocess.run([self.path, *args], capture_output=True, text=True)
        if p.returncode != 0:
            pytest.fail(f"driver {' '.join(args[:1])} exited with {p.returncode}:\n{p.stderr}", pytrace=False)
        return p.stdout

    def capabilities(self):
        return json.loads(self.run("capabilities"))

    def load(self, profile):
        out = self._file("load.json")
        self.run("load", profile, out)
        return nmjson.read(out)

    def edit(self, con, changes):
        inp, ch, out = self._file("in.json"), self._file("changes.json"), self._file("out.json")
        nmjson.write(con, inp)
        with open(ch, "w") as f:
            json.dump(changes, f)
        self.run("edit", inp, ch, out)
        return nmjson.read(out)

    def secrets(self, con, request):
        inp, req, out = self._file("in.json"), self._file("request.json"), self._file("secrets.json")
        nmjson.write(con, inp)
        with open(req, "w") as f:
            json.dump(request, f)
        self.run("secrets", inp, req, out)
        with open(out) as f:
            return json.load(f)


@pytest.fixture(scope="session")
def driver(request, tmp_path_factory):
    return Driver(os.path.abspath(request.config.getoption("--driver")), tmp_path_factory.mktemp("driver"))


@pytest.fixture(scope="session")
def caps(driver):
    return driver.capabilities()


@pytest.fixture(scope="session")
def core():
    return nmjson.load_plugin()


@pytest.fixture(scope="session")
def profiles(tmp_path_factory):
    d = tmp_path_factory.mktemp("profiles")
    shutil.copytree(os.path.join(HERE, "profiles"), d, dirs_exist_ok=True)
    data, _, _ = pkihelp.p12()
    (d / "files" / "client.p12").write_bytes(data)
    (d / "pkcs12.ovpn").write_text("client\nremote vpn.example.net 1194\npkcs12 files/client.p12\n")
    (d / "encrypted-key.ovpn").write_text(
        "client\nremote vpn.example.net 1194\n<ca>\n-----BEGIN CERTIFICATE-----\nMIIBsyntheticTESTCA\n"
        "-----END CERTIFICATE-----\n</ca>\n<key>\n" + pkihelp.pem_key(b"p12pass") + "</key>\n")
    return {name: str(d / f"{name}.ovpn") for name in PROFILES}


@pytest.fixture(scope="session")
def loaded(driver, profiles):
    """Each profile loaded once through the front-end."""
    cache = {}

    def get(name):
        if name not in cache:
            cache[name] = driver.load(profiles[name])
        return cache[name]
    return get


@pytest.fixture(autouse=True)
def snapshot(monkeypatch):
    fake_snapshot(monkeypatch)


def fixture(name):
    return nmjson.read(os.path.join(FIXTURES_DIR, f"{name}.json"))
