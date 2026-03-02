import hashlib
password = "Dodoides81.92"
print(hashlib.sha256(password.encode("utf-8")).hexdigest())
