import time
import hashlib
import os
import hmac

try:
    import bcrypt
    has_bcrypt = True
except ImportError:
    has_bcrypt = False

try:
    import argon2
    has_argon2 = True
except ImportError:
    has_argon2 = False

print(f"Bcrypt available: {has_bcrypt}")
print(f"Argon2 available: {has_argon2}")

password = "TestStrongPassword123!@#"

if has_bcrypt:
    # Measure bcrypt
    t0 = time.perf_counter()
    salt = bcrypt.gensalt(rounds=12)
    h = bcrypt.hashpw(password.encode("utf-8"), salt)
    t1 = time.perf_counter()
    v = bcrypt.checkpw(password.encode("utf-8"), h)
    t2 = time.perf_counter()
    print(f"bcrypt rounds=12: hash={((t1 - t0)*1000):.2f}ms, verify={((t2 - t1)*1000):.2f}ms")

    t0 = time.perf_counter()
    salt10 = bcrypt.gensalt(rounds=10)
    h10 = bcrypt.hashpw(password.encode("utf-8"), salt10)
    t1 = time.perf_counter()
    v10 = bcrypt.checkpw(password.encode("utf-8"), h10)
    t2 = time.perf_counter()
    print(f"bcrypt rounds=10: hash={((t1 - t0)*1000):.2f}ms, verify={((t2 - t1)*1000):.2f}ms")

    t0 = time.perf_counter()
    salt4 = bcrypt.gensalt(rounds=4)
    h4 = bcrypt.hashpw(password.encode("utf-8"), salt4)
    t1 = time.perf_counter()
    v4 = bcrypt.checkpw(password.encode("utf-8"), h4)
    t2 = time.perf_counter()
    print(f"bcrypt rounds=4 (minimum): hash={((t1 - t0)*1000):.2f}ms, verify={((t2 - t1)*1000):.2f}ms")

# PBKDF2-HMAC-SHA256 (OWASP recommended 600,000 iterations for SHA-256)
salt = os.urandom(16)
for iters in [600_000, 210_000, 100_000, 10_000, 1_000]:
    t0 = time.perf_counter()
    k1 = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters)
    t1 = time.perf_counter()
    k2 = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters)
    valid = hmac.compare_digest(k1, k2)
    t2 = time.perf_counter()
    print(f"PBKDF2-HMAC-SHA256 iters={iters}: hash={((t1 - t0)*1000):.2f}ms, verify={((t2 - t1)*1000):.2f}ms")
