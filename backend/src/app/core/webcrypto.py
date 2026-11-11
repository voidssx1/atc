"""Pyodide / Cloudflare Workers Web Crypto interop helpers with explicit `this` receivers."""

from typing import Any, Optional, Tuple

try:
    import js as _js_module
    from pyodide.ffi import to_js as _to_js_fn
except ImportError:
    _js_module = None
    _to_js_fn = None


def get_js_ffi() -> Tuple[Optional[Any], Optional[Any]]:
    """Return `(js_module, to_js_fn)` for the active Pyodide runtime, or `(None, None)`."""
    return _js_module, _to_js_fn


def _resolve_global_this(js_mod: Any) -> Any:
    """Resolve `globalThis` safely so WebIDL getters receive the real global object."""
    try:
        return getattr(js_mod, "globalThis", js_mod)
    except Exception:
        return js_mod


def _reflect_get(reflect_obj: Any, target: Any, prop: str) -> Any:
    if reflect_obj is not None:
        return reflect_obj.get(target, prop, target)
    return getattr(target, prop, None)


def get_subtle_crypto() -> Optional[Any]:
    """
    Lazily resolve `globalThis.crypto.subtle` while preserving JavaScript `this` receivers.

    Avoids top-level import-time access to WebIDL getters (`WorkerGlobalScope.crypto` and
    `Crypto.subtle`) which can throw `Illegal invocation` on Cloudflare Python Workers.
    """
    js_mod, to_js_fn = get_js_ffi()
    if js_mod is None or to_js_fn is None:
        return None

    global_obj = _resolve_global_this(js_mod)
    reflect_obj = getattr(js_mod, "Reflect", None) or getattr(global_obj, "Reflect", None)

    crypto_obj = _reflect_get(reflect_obj, global_obj, "crypto")
    if crypto_obj is None:
        return None

    return _reflect_get(reflect_obj, crypto_obj, "subtle")


def has_web_crypto() -> bool:
    """Return True when Web Crypto `SubtleCrypto` is accessible in the current runtime."""
    return get_subtle_crypto() is not None


async def call_subtle(method_name: str, *args: Any) -> Any:
    """Invoke a `SubtleCrypto` method (`importKey`, `deriveBits`, `verify`) preserving `this`."""
    js_mod, _ = get_js_ffi()
    subtle = get_subtle_crypto()
    if js_mod is None or subtle is None:
        raise RuntimeError("Web Crypto SubtleCrypto is unavailable")

    global_obj = _resolve_global_this(js_mod)
    reflect_obj = getattr(js_mod, "Reflect", None) or getattr(global_obj, "Reflect", None)
    if reflect_obj is not None:
        fn = reflect_obj.get(subtle, method_name, subtle)
        array_ctor = getattr(js_mod, "Array", None) or getattr(global_obj, "Array", None)
        js_args = array_ctor.new(*args) if array_ctor is not None else list(args)
        return await reflect_obj.apply(fn, subtle, js_args)

    return await getattr(subtle, method_name)(*args)
