import asyncio
import base64
import hashlib
import importlib
import sys
import types
from contextlib import contextmanager

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa


class FakeJsException(Exception):
    """Mirrors `pyodide.ffi.JsException` raised on V8/WebIDL receiver mismatches."""


class _StrictSubtleCrypto:
    """Enforces `this === subtle` on `importKey`, `deriveBits`, and `verify`."""

    async def importKey(self, this_obj, fmt, key_data, algorithm, extractable, usages):
        if this_obj is not self:
            raise FakeJsException(
                "TypeError: Illegal invocation: function called with incorrect 'this' reference"
            )
        return {"format": fmt, "key_data": key_data, "algorithm": algorithm, "usages": usages}

    async def deriveBits(self, this_obj, algorithm, base_key, length_bits):
        if this_obj is not self:
            raise FakeJsException(
                "TypeError: Illegal invocation: function called with incorrect 'this' reference"
            )
        dk = hashlib.pbkdf2_hmac(
            "sha256",
            bytes(base_key["key_data"]),
            bytes(algorithm.salt),
            int(algorithm.iterations),
            dklen=int(length_bits) // 8,
        )
        return bytearray(dk)

    async def verify(self, this_obj, algorithm, key, signature, data):
        if this_obj is not self:
            raise FakeJsException(
                "TypeError: Illegal invocation: function called with incorrect 'this' reference"
            )
        jwk = key["key_data"]
        n_int = int.from_bytes(
            base64.urlsafe_b64decode(jwk["n"] + "=" * (-len(jwk["n"]) % 4)), "big"
        )
        e_int = int.from_bytes(
            base64.urlsafe_b64decode(jwk["e"] + "=" * (-len(jwk["e"]) % 4)), "big"
        )
        pub_key = rsa.RSAPublicNumbers(e_int, n_int).public_key()
        try:
            pub_key.verify(
                bytes(signature),
                bytes(data),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
            return True
        except InvalidSignature:
            return False


class _StrictCrypto:
    """Enforces `this === crypto` when reading `.subtle`."""

    def __init__(self) -> None:
        self._subtle = _StrictSubtleCrypto()

    def _get_subtle(self, receiver):
        if receiver is not self:
            raise FakeJsException(
                "TypeError: Illegal invocation: function called with incorrect 'this' reference"
            )
        return self._subtle

    @property
    def subtle(self):
        raise FakeJsException(
            "TypeError: Illegal invocation: function called with incorrect 'this' reference"
        )


class _StrictGlobalThis:
    """Enforces `this === globalThis` when reading `.crypto`."""

    def __init__(self) -> None:
        self._crypto = _StrictCrypto()

    def _get_crypto(self, receiver):
        if receiver is not self:
            raise FakeJsException(
                "TypeError: Illegal invocation: function called with incorrect 'this' reference"
            )
        return self._crypto

    @property
    def crypto(self):
        raise FakeJsException(
            "TypeError: Illegal invocation: function called with incorrect 'this' reference"
        )


class _FakeObject:
    @staticmethod
    def new():
        return types.SimpleNamespace()

    @staticmethod
    def fromEntries(entries):
        return dict(entries)


class _FakeArray:
    @staticmethod
    def new(*items):
        return list(items)


class _FakeUint8Array:
    @staticmethod
    def new(buf):
        return bytes(buf)


class _FakeReflect:
    @staticmethod
    def get(target, prop, receiver=None):
        actual_receiver = target if receiver is None else receiver
        if isinstance(target, _StrictGlobalThis) and prop == "crypto":
            return target._get_crypto(actual_receiver)
        if isinstance(target, _StrictCrypto) and prop == "subtle":
            return target._get_subtle(actual_receiver)
        if isinstance(target, _StrictSubtleCrypto) and prop in ("importKey", "deriveBits", "verify"):
            fn = getattr(type(target), prop)
            return (target, fn)
        return getattr(target, prop, None)

    @staticmethod
    async def apply(target_fn, this_arg, args):
        owner, unbound_fn = target_fn
        if this_arg is not owner:
            raise FakeJsException(
                "TypeError: Illegal invocation: function called with incorrect 'this' reference"
            )
        return await unbound_fn(owner, this_arg, *args)


@contextmanager
def _strict_pyodide_env():
    global_this = _StrictGlobalThis()

    class _FakeJsModule(types.ModuleType):
        Object = _FakeObject
        Array = _FakeArray
        Uint8Array = _FakeUint8Array
        Reflect = _FakeReflect
        globalThis = global_this

        def __getattr__(self, name):
            if name == "crypto":
                # Accessing `js.crypto` without `globalThis` receiver reproduces Cloudflare's crash.
                raise FakeJsException(
                    "TypeError: Illegal invocation: function called with incorrect 'this' reference"
                )
            raise AttributeError(name)

    fake_js = _FakeJsModule("js")
    fake_ffi = types.ModuleType("pyodide.ffi")

    def _to_js(value, dict_converter=None):
        if isinstance(value, dict) and dict_converter is not None:
            return dict_converter(list(value.items()))
        return value

    fake_ffi.to_js = _to_js
    fake_pyodide = types.ModuleType("pyodide")
    fake_pyodide.ffi = fake_ffi

    orig_entries = {
        name: sys.modules.get(name)
        for name in ("js", "pyodide", "pyodide.ffi")
    }
    sys.modules["js"] = fake_js
    sys.modules["pyodide"] = fake_pyodide
    sys.modules["pyodide.ffi"] = fake_ffi

    import app.api.v1.auth as auth_mod
    import app.core.security as sec_mod
    import app.core.webcrypto as wc_mod
    import app.main as main_mod
    import app.services.clerk as clerk_mod
    import worker as worker_mod

    try:
        importlib.reload(wc_mod)
        importlib.reload(sec_mod)
        importlib.reload(clerk_mod)
        importlib.reload(auth_mod)
        importlib.reload(main_mod)
        importlib.reload(worker_mod)
        yield fake_js, wc_mod, sec_mod, clerk_mod
    finally:
        for name, mod in orig_entries.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod
        importlib.reload(wc_mod)
        importlib.reload(sec_mod)
        importlib.reload(clerk_mod)
        importlib.reload(auth_mod)
        importlib.reload(main_mod)
        importlib.reload(worker_mod)


def _b64url_uint(val: int) -> str:
    raw = val.to_bytes((val.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def test_webcrypto_top_level_import_and_pbkdf2_preserve_js_receivers():
    """
    Reproduce the Pyodide/Workers `Illegal invocation` on direct `js.crypto` access
    and verify `security` + `clerk` import cleanly and execute Web Crypto via `Reflect`.
    """
    with _strict_pyodide_env() as (fake_js, wc_mod, sec_mod, clerk_mod):
        # Confirm direct `hasattr(js, "crypto")` / `js.crypto` reproduces the Cloudflare error
        with pytest.raises(FakeJsException, match="Illegal invocation"):
            _ = fake_js.crypto

        # Receiver-safe helper detects SubtleCrypto cleanly without suppressing capability
        assert wc_mod.has_web_crypto() is True

        # Hash & verify password using Web Crypto `importKey` + `deriveBits`
        hashed = asyncio.run(sec_mod.hash_password("BennettDSA!2026", iterations=5_000))
        assert hashed.startswith("$pbkdf2-sha256$i=")
        assert asyncio.run(sec_mod.verify_password("BennettDSA!2026", hashed)) is True
        assert asyncio.run(sec_mod.verify_password("WrongPassword!", hashed)) is False
        # Cross-verify Web Crypto output matches CPython PBKDF2
        assert sec_mod.verify_password_sync("BennettDSA!2026", hashed) is True

        assert clerk_mod is not None


def test_clerk_rs256_webcrypto_verify_preserves_js_receiver():
    """Verify Clerk JWT RS256 verification drives SubtleCrypto with valid `this` receivers."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_numbers = private_key.public_key().public_numbers()
    jwk = {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "n": _b64url_uint(pub_numbers.n),
        "e": _b64url_uint(pub_numbers.e),
    }
    signing_input = b"eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ1c2VyXzEyMyJ9"
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())

    with _strict_pyodide_env() as (_, wc_mod, _, clerk_mod):
        assert wc_mod.has_web_crypto() is True
        assert asyncio.run(
            clerk_mod._verify_rs256_webcrypto(jwk, signing_input, signature)
        ) is True
        assert asyncio.run(
            clerk_mod._verify_rs256_webcrypto(jwk, signing_input + b"x", signature)
        ) is False
